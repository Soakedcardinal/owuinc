"""Unit tests for ETag + If-Match optimistic concurrency in append/edit.

The fake client models a real WebDAV server's three distinct resource states:
missing, present-with-etag and present-without-etag. "ETag is None" is never
used to mean "missing" — that conflation is the exact production bug these
tests guard against.
"""

from aiowebdav2.exceptions import RemoteResourceNotFoundError, ResponseErrorCodeError
from aiowebdav2.models import Property

from owuinc.owuinc import _GETETAG_REQ, Tools


class FakeResource:
    def __init__(self, client):
        self._client = client

    async def read_from(self, buf):
        if not self._client.exists:
            raise RemoteResourceNotFoundError(path="/file.txt")
        buf.write(self._client.content)

    async def write_to(self, buf):
        client = self._client
        client.content = buf.getvalue()
        client.exists = True
        client._bump_etag()


class FakeClient:
    """In-memory WebDAV model that keeps existence and ETag state distinct.

    ``exists`` and ``etag`` are independent: a resource can exist with an ETag,
    exist with ``etag is None`` (a server that exposes no getetag), or be missing
    entirely. Conditional PUTs are enforced like WsgiDAV/Nextcloud:

    - ``If-Match`` fails with 412 unless the value matches the current ETag.
    - ``If-None-Match: *`` fails with 412 if the resource exists at write time.

    Conflict injection (deterministic racy-server simulation):
    - ``conflict_once_change`` rewrites content and bumps the ETag, then returns
      412 on the first If-Match PUT — the classic external-writer race.
    - ``conflict_always`` returns 412 on every If-Match PUT without ever writing.
    - ``create_race_change`` wins the create on the first If-None-Match PUT by
      populating the resource and returning 412.
    """

    def __init__(
        self,
        *,
        exists=True,
        content=b"",
        etag='"e1"',
        server_no_etag=False,
        conflict_once_change=None,
        conflict_always=False,
        create_race_change=None,
    ):
        self.exists = exists
        self.content = content
        self.server_no_etag = server_no_etag
        self.etag = None if server_no_etag else etag
        self._etag_seq = 1
        self.conflict_once_change = conflict_once_change
        self.conflict_always = conflict_always
        self.create_race_change = create_race_change
        self._conflict_once_done = False
        self._create_race_done = False
        self.puts: list[tuple[bytes, dict]] = []
        self.creates: list[tuple[bytes, dict]] = []

    def _bump_etag(self):
        if self.server_no_etag:
            self.etag = None
            return
        self._etag_seq += 1
        self.etag = f'"e{self._etag_seq}"'

    def resource(self, path):
        return FakeResource(self)

    async def check(self, path):
        return self.exists

    async def get_property(self, path, requested):
        assert requested is _GETETAG_REQ
        if not self.exists:
            raise RemoteResourceNotFoundError(path=path)
        if self.etag is None:
            return None
        return Property(
            name=requested.name, namespace=requested.namespace, value=self.etag
        )

    async def execute_request(self, action, path, data=None, headers_ext=None):
        assert action == "upload"
        headers = dict(headers_ext or {})

        if "If-Match" in headers:
            self.puts.append((data, headers))
            if self.conflict_always:
                raise ResponseErrorCodeError(url=path, code=412, message="stale")
            if self.conflict_once_change is not None and not self._conflict_once_done:
                self._conflict_once_done = True
                self.content = self.conflict_once_change
                self._bump_etag()
                raise ResponseErrorCodeError(url=path, code=412, message="stale")
            if headers["If-Match"] != self.etag:
                raise ResponseErrorCodeError(url=path, code=412, message="stale")
            self.content = data
            self._bump_etag()
            return

        if headers.get("If-None-Match") == "*":
            self.creates.append((data, headers))
            if self.exists:
                raise ResponseErrorCodeError(url=path, code=412, message="exists")
            if self.create_race_change is not None and not self._create_race_done:
                self._create_race_done = True
                self.exists = True
                self.content = self.create_race_change
                self._bump_etag()
                raise ResponseErrorCodeError(url=path, code=412, message="exists")
            self.exists = True
            self.content = data
            self._bump_etag()
            return

        self.content = data
        self.exists = True
        self._bump_etag()

    async def close(self):
        pass


def _tools(client):
    t = Tools()
    t.valves.SANDBOX_DIR = ""
    t._webdav_client = lambda: client
    return t


class TestAppendWithEtag:
    async def test_preserves_content_and_sends_if_match(self):
        client = FakeClient(exists=True, content=b"one\n", etag='"e1"')
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "True"
        assert client.content == b"one\ntwo"
        assert len(client.puts) == 1
        assert client.puts[0][1]["If-Match"] == '"e1"'
        assert client.puts[0][0] == b"one\ntwo"

    async def test_no_trailing_newline_inserts_one(self):
        client = FakeClient(exists=True, content=b"one", etag='"e1"')
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "True"
        assert client.content == b"one\ntwo"

    async def test_existing_trailing_newline_not_doubled(self):
        client = FakeClient(exists=True, content=b"one\n", etag='"e1"')
        t = _tools(client)
        res = await t.append("file.txt", "two\n")
        assert res["result"] == "True"
        assert client.content == b"one\ntwo\n"


class TestAppendNoEtag:
    async def test_fails_closed_and_preserves_existing(self):
        client = FakeClient(exists=True, content=b"one\n", server_no_etag=True)
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "False"
        assert "ETag" in res["details"]
        assert client.content == b"one\n"
        assert client.puts == []
        assert client.creates == []


class TestAppendMissing:
    async def test_creates_with_if_none_match(self):
        client = FakeClient(exists=False)
        t = _tools(client)
        res = await t.append("file.txt", "fresh")
        assert res["result"] == "True"
        assert client.content == b"fresh"
        assert client.exists is True
        assert len(client.creates) == 1
        assert client.creates[0][1].get("If-None-Match") == "*"
        assert "If-Match" not in client.creates[0][1]


class TestAppendConflictRetry:
    async def test_412_retry_rereads_with_new_etag(self):
        client = FakeClient(
            exists=True,
            content=b"one\n",
            etag='"e1"',
            conflict_once_change=b"one\nextern\n",
        )
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "True"
        assert client.content == b"one\nextern\ntwo"
        assert client.puts[0][1]["If-Match"] == '"e1"'
        assert client.puts[1][1]["If-Match"] == '"e2"'

    async def test_repeated_412_is_conflict_without_unguarded_write(self):
        client = FakeClient(
            exists=True,
            content=b"one\n",
            etag='"e1"',
            conflict_always=True,
        )
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "False"
        assert "conflict" in res["details"]
        assert len(client.puts) == 2
        assert all("If-Match" in headers for _, headers in client.puts)
        assert client.creates == []
        assert client.content == b"one\n"


class TestAppendConcurrentCreate:
    async def test_create_race_re_appends_instead_of_clobbering(self):
        client = FakeClient(
            exists=False,
            create_race_change=b"winner\n",
        )
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "True"
        assert client.content == b"winner\ntwo"
        assert len(client.creates) == 1
        assert len(client.puts) == 1
        assert "If-Match" in client.puts[0][1]


class TestEditConcurrency:
    async def test_no_etag_fails_closed_and_preserves(self):
        client = FakeClient(exists=True, content=b"alpha", server_no_etag=True)
        t = _tools(client)
        res = await t.edit("file.txt", "alpha", "ALPHA")
        assert res["result"] == "False"
        assert "ETag" in res["details"]
        assert client.content == b"alpha"
        assert client.puts == []

    async def test_missing_file_is_not_found(self):
        client = FakeClient(exists=False)
        t = _tools(client)
        res = await t.edit("file.txt", "a", "b")
        assert res["result"] == "False"
        assert "file not found" in res["details"]

    async def test_retry_rereads_after_conflict(self):
        client = FakeClient(
            exists=True,
            content=b"alpha beta",
            etag='"e1"',
            conflict_once_change=b"alpha beta gamma",
        )
        t = _tools(client)
        res = await t.edit("file.txt", "alpha", "ALPHA")
        assert res["result"] == "True"
        assert client.content == b"ALPHA beta gamma"
        assert client.puts[0][1]["If-Match"] == '"e1"'
        assert client.puts[1][1]["If-Match"] == '"e2"'
