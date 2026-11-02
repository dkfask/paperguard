"""Structured reference rendering shared by checks and DOCX export."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.models.project import ReferenceLanguageFact, ReferenceYearFact


_TYPE_MARKERS = {
    "journal": "J",
    "book": "M",
    "thesis": "D",
    "conference": "C",
    "online": "EB/OL",
    "standard": "S",
}

_LANGUAGE_IDENTITY_FIELDS = (
    "kind", "authors", "title", "source", "collection", "publisher", "institution",
    "organization", "number", "doi", "url", "original_pasted_text", "unformatted_text",
)

_YEAR_IDENTITY_FIELDS = (
    "kind", "authors", "title", "source", "collection", "place", "publisher", "institution",
    "organization", "number", "volume", "issue", "pages", "url", "publish_date", "access_date",
    "doi", "original_pasted_text", "unformatted_text",
)


def _is_explicit_publication_year(value: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{2}", str(value or "").strip()))


def reconcile_reference_language(entry: Any, previous: Any, *, provided: bool,
                                 confirm: bool = False, actor_id: str = "") -> None:
    """Keep confirmation server-owned and invalidate it when its source changes."""
    prior = getattr(previous, "language_fact", None) if previous else None
    fact = entry.language_fact if provided else prior
    if fact is None:
        if confirm:
            raise ValueError("请先选择文献语种")
        entry.language_fact = None
        return
    languages = sorted(set(fact.languages))
    if confirm and (not actor_id or not languages or "und" in languages):
        raise ValueError("请选择已知文献语种后再确认")
    same_identity = previous is not None and all(
        getattr(entry, key, None) == getattr(previous, key, None) for key in _LANGUAGE_IDENTITY_FIELDS
    )
    same_fact = prior is not None and languages == sorted(set(prior.languages)) and fact.evidence == prior.evidence
    if confirm:
        entry.language_fact = ReferenceLanguageFact(
            languages=languages, evidence=fact.evidence, source="student",
            suggested_languages=prior.suggested_languages if prior and same_identity else [], status="confirmed",
            confirmed_by=actor_id, confirmed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    elif same_identity and same_fact:
        entry.language_fact = prior.model_copy(deep=True)
    else:
        entry.language_fact = ReferenceLanguageFact(
            languages=languages, evidence=fact.evidence, source="student",
            suggested_languages=prior.suggested_languages if prior and same_identity else [],
        )


def reconcile_reference_year(entry: Any, previous: Any, *, provided: bool,
                             confirm: bool = False, actor_id: str = "") -> None:
    """Keep publication-year confirmation server-owned and tied to the entry facts."""
    prior = getattr(previous, "year_fact", None) if previous else None
    fact = entry.year_fact if provided else prior
    year = str(getattr(entry, "year", "") or "").strip()
    if fact is None:
        if confirm:
            raise ValueError("请先填写出版年份依据")
        entry.year_fact = None
        return

    same_identity = previous is not None and all(
        getattr(entry, key, None) == getattr(previous, key, None) for key in _YEAR_IDENTITY_FIELDS
    )
    fact_value = str(getattr(fact, "value", "") or "").strip()
    same_fact = prior is not None and fact_value == str(getattr(prior, "value", "") or "").strip() \
        and fact.evidence == prior.evidence

    if confirm:
        if not actor_id or not _is_explicit_publication_year(year) or fact_value != year:
            raise ValueError("请选择有效的四位出版年份后再确认")
        entry.year_fact = ReferenceYearFact(
            value=year, evidence=fact.evidence, source="student",
            suggested_year=prior.suggested_year if prior and same_identity else "",
            status="confirmed", confirmed_by=actor_id,
            confirmed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    elif same_identity and same_fact and prior and prior.value == year:
        entry.year_fact = prior.model_copy(deep=True)
    else:
        entry.year_fact = ReferenceYearFact(
            value=year, evidence=fact.evidence, source="student",
            suggested_year=prior.suggested_year if prior and same_identity else "",
        )


def format_reference(entry: Any, number: int | None = None) -> str:
    """Render a GB/T 7714-shaped entry without guessing missing source data."""
    data = entry.model_dump(mode="json") if hasattr(entry, "model_dump") else dict(entry or {})
    raw = str(data.get("unformatted_text") or "").strip()
    if data.get("kind") == "unparsed":
        return _number_legacy_reference(raw, number)
    if not any(str(data.get(key) or "").strip() for key in ("authors", "title", "source", "collection", "publisher", "institution", "organization")):
        return _number_legacy_reference(raw, number)

    authors = str(data.get("authors") or "").strip()
    title = str(data.get("title") or "").strip()
    marker = _TYPE_MARKERS.get(str(data.get("kind") or "journal"), "J")
    source = str(data.get("source") or "").strip()
    collection = str(data.get("collection") or "").strip()
    publisher = str(data.get("publisher") or "").strip()
    institution = str(data.get("institution") or "").strip()
    organization = str(data.get("organization") or "").strip()
    place = str(data.get("place") or "").strip()
    year = str(data.get("year") or "").strip()
    volume = str(data.get("volume") or "").strip()
    issue = str(data.get("issue") or "").strip()
    pages = str(data.get("pages") or "").strip()
    doi = str(data.get("doi") or "").strip()
    url = str(data.get("url") or "").strip()
    publish_date = str(data.get("publish_date") or "").strip()
    access_date = str(data.get("access_date") or "").strip()

    parts: list[str] = []
    if authors:
        parts.append(authors.rstrip(" .。"))
    if title:
        parts.append(f"{title.rstrip(' .。')}[{marker}]")
    if marker == "J":
        journal = source or collection
        tail = journal
        if year:
            tail += f", {year}" if tail else year
        if volume:
            tail += f", {volume}"
            if issue:
                tail += f"({issue})"
        if pages:
            tail += f": {pages}"
        if tail:
            parts.append(tail)
    elif marker in {"M", "C"}:
        publisher_text = ": ".join(item for item in (place, publisher) if item)
        tail = "，".join(item for item in (collection or source, publisher_text, year) if item)
        if pages:
            tail += f": {pages}" if tail else pages
        if tail:
            parts.append(tail)
    elif marker == "D":
        tail = "，".join(item for item in (place, institution or source, year) if item)
        if pages:
            tail += f": {pages}" if tail else pages
        if tail:
            parts.append(tail)
    elif marker == "S":
        tail = "，".join(item for item in (organization or source, str(data.get("number") or "").strip(), year) if item)
        if tail:
            parts.append(tail)
    else:
        tail = "，".join(item for item in (source, publish_date) if item)
        if url:
            tail += f"{'. ' if tail else ''}{url}"
        if access_date:
            tail += f"（引用日期：{access_date}）"
        if tail:
            parts.append(tail)
    if doi:
        parts.append(f"DOI: {doi}")
    result = ". ".join(part.strip(" .。") for part in parts if part.strip())
    if number is None:
        return result or raw
    return f"[{number}] {(result or _strip_reference_number(raw)).strip()}".strip()


def _strip_reference_number(text: str) -> str:
    return re.sub(r"^\s*(?:\[\s*\d+\s*\]|［\s*\d+\s*］)\s*", "", str(text or "").strip())


def _number_legacy_reference(text: str, number: int | None) -> str:
    raw = str(text or "").strip()
    if number is None or not raw:
        return raw
    return f"[{number}] {_strip_reference_number(raw)}"


def reference_block_text(block: Any, number: int | None = None) -> str:
    reference = getattr(block, "reference", None)
    if reference is not None:
        return format_reference(reference, number)
    text = str(getattr(block, "text", "") or "")
    if number is None:
        return text
    return _number_legacy_reference(text, number)


class ReferenceNumberError(ValueError):
    """A bibliography cannot be numbered without changing an existing citation."""


def _reference_blocks(project: Any) -> list[Any]:
    return [
        block for section in project.sections
        if str(getattr(section.type, "value", section.type)) == "references" or section.key == "references"
        for block in section.paragraphs
        if str(getattr(block.type, "value", block.type)) == "paragraph"
    ]


def _legacy_number(block: Any) -> int | None:
    reference = getattr(block, "reference", None)
    if reference is not None and getattr(reference, "original_pasted_text", None) is not None:
        return None
    text = getattr(reference, "unformatted_text", "") if reference is not None else getattr(block, "text", "")
    match = re.match(r"^\s*\[(\d+)\]", str(text or ""))
    number = int(match.group(1)) if match else None
    if number is not None and number < 1:
        raise ReferenceNumberError("参考文献编号必须从 [1] 开始，请核对后再保存或导出")
    return number


def _explicit_number(block: Any) -> int | None:
    stored = getattr(block, "reference_number", None)
    legacy = _legacy_number(block)
    if stored is not None and legacy is not None and stored != legacy:
        raise ReferenceNumberError(f"参考文献 [{stored}] 的旧文本编号为 [{legacy}]，请核对正文引用后修正")
    return stored or legacy


def reference_number_for(block: Any, position: int) -> int:
    """Use a stored/legacy citation number; position is only a legacy fallback."""
    return _explicit_number(block) or position


def validate_reference_numbers(project: Any) -> None:
    """Reject ambiguous legacy numbering without changing the frozen project."""
    reconcile_reference_numbers(project, project.model_copy(deep=True))


def merge_reference_number_owners(*reservations: dict[str, str]) -> dict[str, str]:
    """Combine historical reservations without transferring an owner's number."""
    merged: dict[str, str] = {}
    by_id: dict[str, str] = {}
    for owners in reservations:
        for number, owner in owners.items():
            if (not isinstance(number, str) or not number.isdigit()
                    or str(int(number)) != number or int(number) < 1
                    or not isinstance(owner, str) or not owner
                    or (number in merged and merged[number] != owner)
                    or (owner in by_id and by_id[owner] != number)):
                raise ReferenceNumberError("历史版本的参考文献编号归属冲突，请核对后再恢复")
            merged[number] = owner
            by_id[owner] = number
    return merged


def reconcile_reference_numbers(previous: Any, updated: Any) -> None:
    """Keep citation numbers owned by the same block, including after deletion."""
    owners: dict[str, str] = dict(getattr(previous, "reference_number_owners", {}) or {})
    by_id: dict[str, int] = {}
    for number, owner in owners.items():
        if not str(number).isdigit() or str(int(number)) != str(number) or int(number) < 1 or not owner or owner in by_id:
            raise ReferenceNumberError("参考文献编号记录异常，请联系教师核查后再保存")
        by_id[owner] = int(number)

    def reserve(block: Any, number: int) -> None:
        key = str(number)
        if number < 1 or (key in owners and owners[key] != block.id) or (block.id in by_id and by_id[block.id] != number):
            raise ReferenceNumberError(f"参考文献 {block.id} 的编号 [{number}] 与已有文献冲突，请核对引用后再保存")
        owners[key] = block.id
        by_id[block.id] = number

    old_blocks = _reference_blocks(previous)
    new_blocks = _reference_blocks(updated)
    for blocks in (old_blocks, new_blocks):
        ids = [block.id for block in blocks]
        if len(ids) != len(set(ids)):
            raise ReferenceNumberError("参考文献存在重复条目标识，请核对后再保存")

    # Old unnumbered entries were exported using their non-empty paragraph
    # position. Preserve that ownership too; never move it to make room for
    # another entry's explicit number.
    position = 0
    for block in old_blocks:
        explicit = _explicit_number(block)
        if reference_block_text(block).strip():
            position += 1
            reserve(block, explicit or by_id.get(block.id) or position)
        elif explicit:
            reserve(block, explicit)

    for block in new_blocks:
        explicit = _explicit_number(block)
        if block.id in by_id:
            if explicit and explicit != by_id[block.id]:
                raise ReferenceNumberError(f"参考文献 {block.id} 的编号已改变，请核对正文引用后再保存")
            block.reference_number = by_id[block.id]
        elif explicit:
            reserve(block, explicit)
            block.reference_number = explicit
    for block in new_blocks:
        if block.id not in by_id and reference_block_text(block).strip():
            number = max((int(n) for n in owners), default=0) + 1
            reserve(block, number)
            block.reference_number = number
    updated.reference_number_owners = owners
