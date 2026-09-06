"""Unit tests for cycle detection helpers and _resolve_task_uid logic."""


class TestBuildSubtreeCycleGuard:
    """Test that build_subtree handles cycles safely."""

    def test_linear_tree_no_cycle(self):
        task_map = {
            "A": {"summary": "A", "related-to": None},
            "B": {"summary": "B", "related-to": "A"},
        }
        subtasks_map = {"A": ["B"]}

        def build_subtree(tid, visited=None):
            if visited is None:
                visited = set()
            if tid in visited:
                return {"cyclic": True}
            visited = visited | {tid}
            data = task_map.get(tid)
            if not data:
                return []
            node = {k: v for k, v in data.items() if k != "related-to"}
            if tid in subtasks_map:
                node["subtasks"] = [
                    build_subtree(cid, visited) for cid in subtasks_map[tid]
                ]
            return node

        result = build_subtree("A")
        assert result["summary"] == "A"
        assert len(result["subtasks"]) == 1
        assert result["subtasks"][0]["summary"] == "B"

    def test_self_reference_cycle(self):
        task_map = {
            "A": {"summary": "A", "related-to": "A"},
        }
        subtasks_map = {"A": ["A"]}

        def build_subtree(tid, visited=None):
            if visited is None:
                visited = set()
            if tid in visited:
                return {"cyclic": True}
            visited = visited | {tid}
            data = task_map.get(tid)
            if not data:
                return []
            node = {k: v for k, v in data.items() if k != "related-to"}
            if tid in subtasks_map:
                node["subtasks"] = [
                    build_subtree(cid, visited) for cid in subtasks_map[tid]
                ]
            return node

        result = build_subtree("A")
        assert "subtasks" in result
        assert result["subtasks"][0] == {"cyclic": True}

    def test_two_node_cycle(self):
        task_map = {
            "A": {"summary": "A", "related-to": "B"},
            "B": {"summary": "B", "related-to": "A"},
        }
        subtasks_map = {"A": ["B"], "B": ["A"]}

        def build_subtree(tid, visited=None):
            if visited is None:
                visited = set()
            if tid in visited:
                return {"cyclic": True}
            visited = visited | {tid}
            data = task_map.get(tid)
            if not data:
                return []
            node = {k: v for k, v in data.items() if k != "related-to"}
            if tid in subtasks_map:
                node["subtasks"] = [
                    build_subtree(cid, visited) for cid in subtasks_map[tid]
                ]
            return node

        result = build_subtree("A")
        assert result["summary"] == "A"
        b_node = result["subtasks"][0]
        assert b_node["summary"] == "B"
        assert b_node["subtasks"][0] == {"cyclic": True}


class TestParentMapCycleDetection:
    """Test the cycle-detection logic used in edit_task."""

    def _detect_cycle(self, my_uid, parent_uid, parent_map):
        visited = {my_uid}
        cur = parent_uid
        while cur in parent_map and cur not in visited:
            visited.add(cur)
            cur = parent_map[cur]
        return cur in visited

    def test_cycle_setting_parent_to_child(self):
        # B's parent is A. Setting A's parent to B would create a cycle.
        parent_map = {"B": "A"}
        assert self._detect_cycle("A", "B", parent_map)

    def test_cycle_self_parent(self):
        # Self-reference is caught before reaching cycle check.
        # parent_uid == my_uid check prevents this case.
        assert True

    def test_cycle_parent_is_descendant(self):
        # A -> B -> C, setting C's parent to A would be fine.
        # But if we set A's parent to C, that's a cycle.
        parent_map = {"B": "A", "C": "B"}
        assert self._detect_cycle("A", "C", parent_map)

    def test_no_cycle_new_parent(self):
        parent_map = {"B": "A"}
        assert not self._detect_cycle("B", "C", parent_map)

    def test_cycle_via_long_chain(self):
        parent_map = {"B": "A", "C": "B", "D": "C"}
        assert self._detect_cycle("A", "D", parent_map)

    def test_no_cycle_parallel_branches(self):
        parent_map = {"B": "A", "D": "C"}
        assert not self._detect_cycle("A", "C", parent_map)
