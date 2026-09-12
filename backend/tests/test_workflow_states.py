"""Canonical state machine contract tests."""

import pytest

from app.domain.workflow_states import transition_state


@pytest.mark.parametrize("entity, current, target", [
    ("participant", "in_progress", "submitted"),
    ("participant", "pending_archive", "archived"),
    ("submission", "submitted", "returned"),
    ("process_record", "returned", "submitted"),
    ("comment", "addressed", "resolved"),
    ("archive", "running", "succeeded"),
])
def test_known_workflow_transitions(entity, current, target):
    assert transition_state(entity, current, target) == target


@pytest.mark.parametrize("entity, current, target", [
    ("submission", "approved", "submitted"),
    ("process_record", "approved", "returned"),
    ("comment", "resolved", "addressed"),
    ("archive", "succeeded", "queued"),
    ("unknown", "draft", "submitted"),
])
def test_invalid_workflow_transitions_fail_closed(entity, current, target):
    with pytest.raises(ValueError):
        transition_state(entity, current, target)
