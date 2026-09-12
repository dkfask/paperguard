"""Canonical workflow states shared by API, worker and future clients."""

from __future__ import annotations

from typing import Final

PARTICIPANT_STATES: Final[frozenset[str]] = frozenset({
    "not_started", "in_progress", "submitted", "needs_revision",
    "approved", "pending_archive", "archived", "exited", "withdrawn",
})
ACTIVE_PARTICIPANT_STATES: Final[frozenset[str]] = frozenset({
    "not_started", "in_progress", "needs_revision",
})
SUBMISSION_STATES: Final[frozenset[str]] = frozenset({
    "submitted", "returned", "approved", "withdrawn",
})
ARCHIVE_JOB_STATES: Final[frozenset[str]] = frozenset({
    "queued", "running", "succeeded", "failed",
})
PROJECT_EXPORT_STATES: Final[frozenset[str]] = ARCHIVE_JOB_STATES
DOCUMENT_RENDER_JOB_STATES: Final[frozenset[str]] = frozenset({*ARCHIVE_JOB_STATES, "cancelled"})

# One transition table is shared by HTTP mutations and the standalone worker.
# Creation states are assigned when the record is first persisted; every later
# mutation must pass through ``transition_state``.
STATE_TRANSITIONS: Final[dict[str, dict[str, frozenset[str]]]] = {
    "participant": {
        "not_started": frozenset({"in_progress", "submitted", "exited"}),
        "in_progress": frozenset({"submitted", "needs_revision", "exited"}),
        "submitted": frozenset({"in_progress", "needs_revision", "approved", "pending_archive", "exited"}),
        "needs_revision": frozenset({"submitted", "exited"}),
        "approved": frozenset({"in_progress"}),
        "pending_archive": frozenset({"in_progress", "archived"}),
        "archived": frozenset({"in_progress"}),
        "exited": frozenset(),
        "withdrawn": frozenset({"in_progress"}),
    },
    "submission": {
        "submitted": frozenset({"returned", "approved", "withdrawn"}),
        "returned": frozenset(),
        "approved": frozenset(),
        "withdrawn": frozenset(),
    },
    "process_record": {
        "draft": frozenset({"submitted"}),
        "submitted": frozenset({"approved", "returned", "rejected"}),
        "returned": frozenset({"submitted"}),
        "approved": frozenset(),
        "rejected": frozenset(),
    },
    "comment": {
        "open": frozenset({"addressed", "resolved"}),
        "addressed": frozenset({"open", "resolved"}),
        "resolved": frozenset(),
    },
    "archive": {
        "queued": frozenset({"running", "failed"}),
        "running": frozenset({"queued", "succeeded", "failed"}),
        "failed": frozenset({"queued"}),
        "succeeded": frozenset(),
    },
    "project_export": {
        "queued": frozenset({"running", "failed"}),
        "running": frozenset({"queued", "succeeded", "failed"}),
        "failed": frozenset({"queued"}),
        "succeeded": frozenset(),
    },
    "document_render_job": {
        "queued": frozenset({"running", "failed", "cancelled"}),
        "running": frozenset({"queued", "succeeded", "failed", "cancelled"}),
        "failed": frozenset({"queued", "cancelled"}),
        "cancelled": frozenset(),
        "succeeded": frozenset(),
    },
}


def is_known_state(state: str, states: frozenset[str]) -> bool:
    return state in states


def transition_state(entity: str, current: str, target: str) -> str:
    """Validate and return a canonical state transition."""
    if current == target:
        return target
    transitions = STATE_TRANSITIONS.get(entity)
    if transitions is None or current not in transitions or target not in transitions[current]:
        raise ValueError(f"invalid {entity} transition: {current} -> {target}")
    return target
