from __future__ import annotations

from placetype_ph.llm.mock import MockBackend
from placetype_ph.traversal import HierarchicalTraverser


def test_stop_here_is_valid_partial_classification(toy_psic):
    # One pass: choose A -> 10 -> 101 -> STOP at group 101.
    backend = MockBackend(
        [
            '{"decision":"A","reason":"activity in A"}',
            '{"decision":"10","reason":"food evidence"}',
            '{"decision":"101","reason":"processing"}',
            '{"decision":"STOP_HERE","reason":"cannot distinguish class"}',
        ]
    )
    traverser = HierarchicalTraverser(toy_psic, backend, passes=1, temperature=0)
    result = traverser.classify("small food processor")
    assert result.code == "101"
    assert result.path == ["A", "10", "101"]
    assert result.agreement == 1.0


def test_insufficient_retracts_the_current_node(toy_psic):
    backend = MockBackend(
        [
            '{"decision":"A","reason":"maybe A"}',
            '{"decision":"10","reason":"maybe food"}',
            '{"decision":"INSUFFICIENT","reason":"10 itself is unsupported"}',
        ]
    )
    traverser = HierarchicalTraverser(toy_psic, backend, passes=1, temperature=0)
    result = traverser.classify("ambiguous evidence")
    assert result.code == "A"
    assert result.path == ["A"]
    assert "INSUFFICIENT_BACKOFF" in result.flags
