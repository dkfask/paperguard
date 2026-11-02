"""Stable Word bookmark names for generated manuscript content."""
from __future__ import annotations

import hashlib

from app.models.project import BlockType


def block_bookmark_name(block_id: str, role: str) -> str:
    """Keep names deterministic, private, and within Word's 40-character limit."""
    prefixes = {"start": "PGB", "end": "PGE", "caption": "PGC"}
    return prefixes[role] + hashlib.sha256(block_id.encode("utf-8")).hexdigest()[:32]


def required_layout_bookmarks(project, profile) -> list[str]:
    """List every heading and generated content boundary requiring measurement.

    This follows the same section and block selection as the master renderer.
    Names, rather than manuscript text, are carried in the layout receipt.
    """
    headings = [heading for heading in profile.get("headings", []) if heading.get("kind") != "toc"]
    by_key = {heading["key"]: heading for heading in headings}
    names = [heading.get("bookmark_name", "") for heading in headings]
    for section in project.sections:
        if section.key not in by_key:
            continue
        for block in section.paragraphs:
            names.extend(block_bookmark_name(block.id, role) for role in ("start", "end"))
            if ((block.type == BlockType.IMAGE and block.image)
                    or (block.type == BlockType.TABLE and block.table)):
                names.append(block_bookmark_name(block.id, "caption"))
    return names


def required_table_row_targets(project, profile) -> list[dict]:
    """Identify generated tables whose row locations require Word measurement."""
    headings = {heading["key"] for heading in profile.get("headings", [])
                if heading.get("kind") != "toc"}
    targets = []
    for section in project.sections:
        if section.key not in headings:
            continue
        for block in section.paragraphs:
            if block.type == BlockType.TABLE and block.table:
                targets.append({"bookmark": block_bookmark_name(block.id, "start"),
                                "end_bookmark": block_bookmark_name(block.id, "end"),
                                "row_count": block.table.rows})
    return targets
