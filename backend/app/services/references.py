"""Structured reference rendering shared by checks and DOCX export."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from app.models.project import InlineTextRun, ReferenceLanguageFact, ReferenceYearFact


_REFERENCE_NUMBER_PREFIX = re.compile(r"^\s*(?:\[\s*(\d+)\s*\]|［\s*(\d+)\s*］)")

_TYPE_MARKERS = {
    "journal": "J",
    "book": "M",
    "thesis": "D",
    "conference": "C",
    "online": "EB/OL",
    "standard": "S",
}

_PASTED_HEADER = re.compile(
    r"^([^。.]+)[.。]\s*([^。.]+?)\s*\[(J|M|D)\](?:\s*[.。]\s*|\s*$)(.*)$",
    re.IGNORECASE,
)
_PASTED_PAGES = r"[a-z]?\d+(?:[-–—][a-z]?\d+)?(?:\s*\+\s*[a-z]?\d+(?:[-–—][a-z]?\d+)?)*"
_FACT_LABELS = {
    "authors": "作者", "title": "题名", "source": "来源", "collection": "文集",
    "place": "出版地", "publisher": "出版社", "institution": "授予单位",
    "organization": "发布机构", "number": "标准编号", "year": "年份", "volume": "卷号",
    "issue": "期号", "pages": "页码", "url": "网址", "publish_date": "发布日期",
    "access_date": "引用日期", "doi": "DOI",
}
_REQUIRED_FACTS = {
    "journal": (("authors",), ("title",), ("source", "collection"), ("year",)),
    "book": (("authors",), ("title",), ("publisher",), ("year",)),
    "thesis": (("authors",), ("title",), ("institution", "source"), ("year",)),
    "conference": (("authors",), ("title",), ("collection", "source"), ("year",)),
    "online": (("authors",), ("title",), ("url",)),
    "standard": (("title",), ("number",)),
}
_RENDERED_FACTS = {
    "journal": {"authors", "title", "source", "collection", "year", "volume", "issue", "pages", "doi"},
    "book": {"authors", "title", "source", "collection", "place", "publisher", "year", "pages", "doi"},
    "thesis": {"authors", "title", "source", "place", "institution", "year", "pages", "doi"},
    "conference": {"authors", "title", "source", "collection", "place", "publisher", "year", "pages", "doi"},
    "online": {"authors", "title", "source", "publish_date", "year", "url", "access_date", "doi"},
    "standard": {"authors", "title", "source", "organization", "number", "place", "publisher", "year", "doi"},
}


def _parse_pasted_reference(text: str) -> tuple[str, dict[str, str]] | None:
    """Independently parse only complete, unambiguous source forms we support.

    A failed parse is unknown, not evidence that all source facts were copied.
    """
    source_text = _REFERENCE_NUMBER_PREFIX.sub("", text or "", count=1)
    source_text = re.sub(r"\s*\n\s*", " ", source_text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    header = _PASTED_HEADER.fullmatch(source_text)
    if not header:
        return None
    authors, title, marker, tail = header.groups()
    fields = {"authors": authors.strip(), "title": title.strip()}
    if not all(fields.values()):
        return None
    kind = {"J": "journal", "M": "book", "D": "thesis"}[marker.upper()]
    tail = tail.strip()
    # A source may omit publication facts altogether. Those facts can then be
    # supplied by the student without changing or discarding the pasted text.
    if not tail:
        return kind, fields
    if kind == "journal":
        doi = re.search(r"(?:[.。:：]\s*|\s+|(?<=\d))DOI\s*[:：]\s*(10\.\d{4,9}/\S+?)\s*[.。]?\s*$", tail, re.I)
        if doi:
            fields["doi"] = doi.group(1)
            tail = tail[:doi.start()].strip()
        separators = list(re.finditer(r"[,，]\s*(?=(?:19|20)\d{2}(?=\s*(?:[,，.:：]|$)))", tail))
        if not separators and not doi and not re.search(r"\d|[:：\[\]]", tail):
            fields["source"] = tail.rstrip(" .。").strip()
            return (kind, fields) if fields["source"] else None
        if len(separators) != 1:
            return None
        separator = separators[0]
        journal = re.fullmatch(
            rf"((?:19|20)\d{{2}})(?:\s*[,，]\s*(?:(\d+)(?:\(([^()]+)\))?|\(([^()]+)\)))?"
            rf"(?:\s*[:：]\s*({_PASTED_PAGES})\s*)?\s*[.。]?", tail[separator.end():], re.I,
        )
        fields["source"] = tail[:separator.start()].strip()
        if not journal or not fields["source"]:
            return None
        fields["year"] = journal.group(1)
        for key, value in (("volume", journal.group(2)), ("issue", journal.group(3) or journal.group(4)),
                           ("pages", journal.group(5))):
            if value:
                fields[key] = value.strip()
    else:
        separators = list(re.finditer(r"[,，]\s*(?=(?:19|20)\d{2})", tail))
        if not separators and not re.search(r"\d|[,，\[\]]", tail):
            publication = [part.strip(" .。") for part in re.split(r"[:：]", tail)]
            if 0 < len(publication) <= 2 and all(publication):
                if len(publication) == 2:
                    fields["place"] = publication[0]
                fields["publisher" if kind == "book" else "institution"] = publication[-1]
                return kind, fields
        if len(separators) != 1:
            return None
        separator = separators[0]
        publication = [part.strip() for part in re.split(r"[:：]", tail[:separator.start()].strip())]
        remainder = re.fullmatch(rf"((?:19|20)\d{{2}})(?:\s*[:：]\s*({_PASTED_PAGES})\s*)?\s*[.。]?",
                                 tail[separator.end():], re.I)
        if not remainder or len(publication) > 2 or not all(publication):
            return None
        if len(publication) == 2:
            fields["place"] = publication[0]
        fields["publisher" if kind == "book" else "institution"] = publication[-1]
        fields["year"] = remainder.group(1)
        if remainder.group(2):
            fields["pages"] = remainder.group(2).strip()
    return kind, fields


def _pasted_facts_preserved(entry: Any, original: str) -> bool:
    """Match the supplied header, then parse remaining source facts by position."""
    kind = getattr(entry, "kind", "")
    if kind not in {"journal", "book", "thesis"}:
        return False

    def normalized(value: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip(" .。")

    text = normalized(_REFERENCE_NUMBER_PREFIX.sub("", original, count=1))
    authors = normalized(str(getattr(entry, "authors", "") or ""))
    title = normalized(str(getattr(entry, "title", "") or ""))
    if not authors or not title:
        return False
    marker = _TYPE_MARKERS[kind]
    header = re.match(re.escape(authors) + r"\s*[.。]\s*" + re.escape(title)
                      + r"\s*\[\s*" + re.escape(marker.casefold()) + r"\s*\]", text)
    if not header:
        return False
    parsed = _parse_pasted_reference(f"author. title[{marker}]{text[header.end():]}")
    if parsed is None or parsed[0] != kind or parsed[1].get("authors") != "author" or parsed[1].get("title") != "title":
        return False
    return all(normalized(str(getattr(entry, field, "") or "")) == normalized(value)
               for field, value in parsed[1].items() if field not in {"authors", "title"})


def validate_reference_facts(entry: Any) -> list[str]:
    """Check supported facts and source preservation without claiming a standard version."""
    kind = getattr(entry, "kind", "")
    if kind == "unparsed":
        return ["尚未识别文献类型及事实字段，原文已保留，请补录"]
    problems = []
    for choices in _REQUIRED_FACTS.get(kind, ()):
        if not any(str(getattr(entry, field, "") or "").strip().rstrip(" .。") for field in choices):
            problems.append("缺少" + "或".join(_FACT_LABELS[field] for field in choices))
    year = str(getattr(entry, "year", "") or "").strip()
    if year and not re.fullmatch(r"\d{4}", year):
        problems.append("年份无法识别，请填写四位年份")

    original = getattr(entry, "original_pasted_text", None)
    if original is None:
        original = str(getattr(entry, "unformatted_text", "") or "").strip() or None
    if original is not None:
        parsed = _parse_pasted_reference(original)
        if parsed is None and not _pasted_facts_preserved(entry, original):
            problems.append("原文含尚不能完成自动核验的内容，已保留原文，请联系系统适配")
        elif parsed is not None and parsed[0] != kind:
            problems.append("文献类型与粘贴原文不一致，请核对")
        elif parsed is not None:
            for field, source_value in parsed[1].items():
                value = str(getattr(entry, field, "") or "").strip()
                if value != source_value:
                    problems.append(f"{_FACT_LABELS[field]}与粘贴原文不一致，请核对")

    rendered = format_reference(entry)
    rendered_fields = set(_RENDERED_FACTS.get(kind, ()))
    preferred = {"journal": ("source", "collection"), "book": ("collection", "source"),
                 "conference": ("collection", "source"), "thesis": ("institution", "source"),
                 "standard": ("organization", "source")}.get(kind)
    if preferred and all(str(getattr(entry, field, "") or "").strip() for field in preferred):
        rendered_fields.discard(preferred[1])
    for field, label in _FACT_LABELS.items():
        value = str(getattr(entry, field, "") or "").strip()
        if value and (field not in rendered_fields or value.rstrip(" .。") not in rendered):
            problems.append(f"{label}未能保留在生成的著录中，请核对")
    return problems

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
        if volume or issue:
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
        publication = ": ".join(item for item in (place, publisher) if item)
        tail = "，".join(item for item in (organization or source, str(data.get("number") or "").strip(), publication, year) if item)
        if tail:
            parts.append(tail)
    else:
        display_year = year if year and not publish_date.startswith(year) else ""
        tail = "，".join(item for item in (source, publish_date, display_year) if item)
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
    return _REFERENCE_NUMBER_PREFIX.sub("", str(text or "").strip(), count=1).lstrip()


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


class ReferenceInlineMappingError(ValueError):
    """Reference text was reformatted in a way that cannot retain inline styling."""


def reference_inline_runs(block: Any, number: int | None) -> list[InlineTextRun]:
    """Map source spans to the exact numbered reference without inventing formatting.

    Only the generated number and surrounding whitespace may be normalized.
    A structured entry that rewrites the body must be reconciled before export.
    """
    runs = block.inline_runs
    if not runs:
        return []
    source = str(block.text or "")
    if "".join(run.text for run in runs) != source:
        raise ReferenceInlineMappingError("参考文献的行内格式与原文不一致，请重新编辑该条目")
    rendered = reference_block_text(block, number)
    if source == rendered:
        return [run.model_copy(deep=True) for run in runs if run.text]
    if number is None:
        raise ReferenceInlineMappingError("参考文献的结构化内容与原文行内格式无法对应，请重新编辑该条目")

    source_start = len(source) - len(source.lstrip())
    marker = _REFERENCE_NUMBER_PREFIX.match(source[source_start:])
    body_start = source_start + (marker.end() if marker else 0)
    while body_start < len(source) and source[body_start].isspace():
        body_start += 1
    body_end = len(source.rstrip())
    prefix = f"[{number}] "
    if not rendered.startswith(prefix) or rendered[len(prefix):] != source[body_start:body_end]:
        raise ReferenceInlineMappingError("参考文献的结构化内容与原文行内格式无法对应，请重新编辑该条目")

    def slice_runs(start: int, end: int) -> list[InlineTextRun]:
        result = []
        offset = 0
        for run in runs:
            right = offset + len(run.text)
            part = run.text[max(0, start - offset):min(len(run.text), end - offset)]
            if right > start and offset < end and part:
                result.append(run.model_copy(update={"text": part}, deep=True))
            offset = right
        return result

    # Keep formatting on an existing number if it has one unambiguous style.
    marker_runs = slice_runs(source_start, source_start + marker.end()) if marker else []
    marker_styles = {
        (run.bold, run.italic, run.underline, run.highlight, run.vert_align)
        for run in marker_runs if run.text.strip()
    }
    if len(marker_styles) > 1:
        raise ReferenceInlineMappingError("参考文献编号的行内格式无法安全规范化，请重新编辑该条目")
    if marker_styles:
        bold, italic, underline, highlight, vert_align = next(iter(marker_styles))
        number_run = InlineTextRun(text=prefix[:-1], bold=bold, italic=italic,
                                   underline=underline, highlight=highlight, vert_align=vert_align)
    else:
        number_run = InlineTextRun(text=prefix[:-1])
    return [number_run, InlineTextRun(text=" "), *slice_runs(body_start, body_end)]


def validate_reference_inline_formats(project: Any) -> list[str]:
    """Report references that cannot preserve their formatting during export."""
    problems = []
    for section in project.sections:
        if str(getattr(section.type, "value", section.type)) != "references":
            continue
        number = 0
        for block in section.paragraphs:
            if str(getattr(block.type, "value", block.type)) != "paragraph" or not reference_block_text(block).strip():
                continue
            number += 1
            if block.inline_runs:
                try:
                    reference_inline_runs(block, reference_number_for(block, number))
                except ReferenceInlineMappingError as exc:
                    problems.append(f"参考文献第 {number} 条：{exc}")
    return problems


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
    match = _REFERENCE_NUMBER_PREFIX.match(str(text or ""))
    number = int(match.group(1) or match.group(2)) if match else None
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
