"""Unit tests for ETag + If-Match optimistic concurrency in append/edit."""

from aiowebdav2.exceptions import RemoteResourceNotFoundError, ResponseErrorCodeError
from aiowebdav2.models import Property

from owuinc.owuinc import _GETETAG_REQ, Tools


class FakeResource:
    def __init__(self, store):
        self._store = store

    async def read_from(self, buf):
        if self._store["content"] is None:
            raise RemoteResourceNotFoundError(path="/file.txt")
        buf.write(self._store["content"])

    async def write_to(self, buf):
        self._store["content"] = buf.getvalue()


class FakeClient:
    """Serves scripted ETags/content and fails the first conditional PUT."""

    def __init__(self, etags, initial, change_on_conflict=None):
        self._etags = list(etags)
        self.store = {"content": initial}
        self.change_on_conflict = change_on_conflict
        self.puts: list[tuple[bytes, dict]] = []
        self._propfinds = 0

    def resource(self, path):
        return FakeResource(self.store)

    async def get_property(self, path, requested):
        assert requested is _GETETAG_REQ
        value = self._etags[self._propfinds]
        self._propfinds += 1
        if value is None:
            raise RemoteResourceNotFoundError(path=path)
        return Property(name=requested.name, namespace=requested.namespace, value=value)

    async def execute_request(self, action, path, data=None, headers_ext=None):
        assert action == "upload"
        self.puts.append((data, dict(headers_ext or {})))
        if len(self.puts) == 1 and self.change_on_conflict is not None:
            self.store["content"] = self.change_on_conflict
            raise ResponseErrorCodeError(url=path, code=412, message="stale")
        self.store["content"] = data

    async def close(self):
        pass


def _tools(client):
    t = Tools()
    t.valves.SANDBOX_DIR = ""
    t._webdav_client = lambda: client
    return t


class TestAppendConcurrency:
    async def test_retry_rereads_after_conflict(self):
        client = FakeClient(
            etags=['"e1"', '"e2"'],
            initial=b"one\n",
            change_on_conflict=b"one\nextern\n",
        )
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "True"
        assert client.store["content"] == b"one\nextern\ntwo"
        assert client.puts[0][1]["If-Match"] == '"e1"'
        assert client.puts[1][1]["If-Match"] == '"e2"'

    async def test_conflict_twice_is_error(self):
        client = FakeClient(
            etags=['"e1"', '"e2"'],
            initial=b"one\n",
            change_on_conflict=b"one\nextern\n",
        )

        async def always_conflict(action, path, data=None, headers_ext=None):
            client.puts.append((data, dict(headers_ext or {})))
            raise ResponseErrorCodeError(url=path, code=412, message="stale")

        client.execute_request = always_conflict
        t = _tools(client)
        res = await t.append("file.txt", "two")
        assert res["result"] == "False"
        assert "conflict" in res["details"]

    async def test_missing_file_creates_directly(self):
        client = FakeClient(etags=[None], initial=None)
        t = _tools(client)
        res = await t.append("file.txt", "fresh")
        assert res["result"] == "True"
        assert client.store["content"] == b"fresh"
        assert client.puts == []


class TestEditConcurrency:
    async def test_retry_rereads_after_conflict(self):
        client = FakeClient(
            etags=['"e1"', '"e2"'],
            initial=b"alpha beta",
            change_on_conflict=b"alpha beta gamma",
        )
        t = _tools(client)
        res = await t.edit("file.txt", "alpha", "ALPHA")
        assert res["result"] == "True"
        assert client.store["content"] == b"ALPHA beta gamma"
        assert client.puts[1][1]["If-Match"] == '"e2"'

    async def test_missing_file_is_not_found(self):
        client = FakeClient(etags=[None], initial=None)
        t = _tools(client)
        res = await t.edit("file.txt", "a", "b")
        assert res["result"] == "False"
        assert "file not found" in res["details"]
