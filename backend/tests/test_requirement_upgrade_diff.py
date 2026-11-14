"""Requirement upgrades must describe actual changes, not snapshot timestamps."""

from app.api.projects import _snapshot_diff
from app.services.policy_resolver import content_hash


def _snapshot(created_at, items):
    snapshot = {"snapshot_created_at": created_at, "policy_sets": [], "resolved_items": items}
    snapshot["content_hash"] = content_hash(snapshot)
    return snapshot


def test_creation_time_alone_is_not_a_requirement_upgrade():
    item = {"id": "body", "description": "正文字数", "params": {"min": 8000}}
    old = _snapshot("2026-11-12T10:00:00+00:00", [item])
    new = _snapshot("2026-11-12T10:01:00+00:00", [item])

    assert _snapshot_diff(old, new)["changed"] is False


def test_upgrade_diff_includes_changed_values_for_an_existing_requirement():
    old_item = {"id": "body", "description": "正文字数", "params": {"min": 8000, "max": 25}}
    new_item = {"id": "body", "description": "正文字数", "params": {"min": 8000}}
    old = _snapshot("2026-11-12T10:00:00+00:00", [old_item])
    new = _snapshot("2026-11-12T10:01:00+00:00", [new_item])

    diff = _snapshot_diff(old, new)

    assert diff["changed"] is True
    assert diff["changed_items"] == [{"before": old_item, "after": new_item}]
