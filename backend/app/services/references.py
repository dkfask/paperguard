"""Structured reference rendering shared by checks and DOCX export."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Literal

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
    r"^([^。.]+)[.。]\s*([^。.]+?)\s*\[(EB/OL|J|M|D|C|S)\](?:\s*[.。]\s*|\s*$)(.*)$",
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
    standard_only = None if header else re.fullmatch(
        r"([^。.]+?)\s*\[(S)\](?:\s*[.。]\s*|\s*$)(.*)", source_text, re.I,
    )
    if standard_only:
        title, marker, tail = standard_only.groups()
        fields = {"title": title.strip()}
    elif header:
        authors, title, marker, tail = header.groups()
        fields = {"authors": authors.strip(), "title": title.strip()}
    else:
        return None
    if not all(fields.values()):
        return None
    kind = {"J": "journal", "M": "book", "D": "thesis", "C": "conference",
            "EB/OL": "online", "S": "standard"}[marker.upper()]
    tail = tail.strip()
    if kind != "journal":
        doi = re.search(r"(?:[.。]\s*|\s+)DOI\s*[:：]\s*(10\.\d{4,9}/\S+?)\s*[.。]?\s*$", tail, re.I)
        if doi:
            before = tail[:doi.start()].strip()
            safe_online_separator = doi.group(0)[0].isspace() or re.search(
                r"（引用日期[:：]\s*\d{4}-\d{2}-\d{2}）$", before,
            )
            if kind != "online" or safe_online_separator:
                fields["doi"] = doi.group(1)
                tail = before
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
    elif kind == "conference":
        parts = re.fullmatch(
            rf"([^,，.。:：]+)\s*[,，]\s*([^,，.。:：]+)\s*[:：]\s*([^,，.。:：]+)"
            rf"\s*[,，]\s*((?:19|20)\d{{2}})(?:\s*[:：]\s*({_PASTED_PAGES})\s*)?\s*[.。]?", tail, re.I,
        )
        if parts:
            fields.update(collection=parts.group(1).strip(), place=parts.group(2).strip(),
                          publisher=parts.group(3).strip(), year=parts.group(4))
            if not all(fields[key] for key in ("collection", "place", "publisher")):
                return None
            if parts.group(5):
                fields["pages"] = parts.group(5).strip()
        elif not re.search(r"[\d,，:：\[\].。]", tail):
            fields["collection"] = tail
        else:
            return None
    elif kind == "online":
        parts = re.fullmatch(
            r"(?:([^。.]+?)[.。]\s*)?(https?://[^\s（）]+?)(?:（引用日期[:：]\s*(\d{4}-\d{2}-\d{2})）)?",
            tail, re.I,
        )
        if not parts or parts.group(2).endswith((".", "。")):
            return None
        prefix = (parts.group(1) or "").strip()
        dated = re.fullmatch(r"([^,，]+?)[,，]\s*(\d{4}(?:-\d{2}(?:-\d{2})?)?)", prefix)
        if dated:
            fields.update(source=dated.group(1).strip(), publish_date=dated.group(2))
        elif re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", prefix):
            fields["publish_date"] = prefix
        elif prefix and not re.search(r"[,，]", prefix):
            fields["source"] = prefix
        elif prefix:
            return None
        fields["url"] = parts.group(2)
        if parts.group(3):
            fields["access_date"] = parts.group(3)
    elif kind == "standard":
        parts = re.fullmatch(
            r"([A-Za-z][A-Za-z0-9/.\s-]*\d(?:[A-Za-z0-9/.-]*[A-Za-z0-9])?)"
            r"(?:\s*[,，]\s*([^,，.。:：]+)\s*[:：]\s*([^,，.。:：]+))?"
            r"(?:\s*[,，]\s*((?:19|20)\d{2}))?\s*[.。]?", tail,
        )
        if not parts:
            return None
        fields["number"] = parts.group(1).strip()
        if parts.group(2):
            fields.update(place=parts.group(2).strip(), publisher=parts.group(3).strip())
            if not fields["place"] or not fields["publisher"]:
                return None
        if parts.group(4):
            fields["year"] = parts.group(4)
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
    if kind not in _TYPE_MARKERS:
        return False

    # Preserve the original URL and other source facts exactly for these types:
    # URL paths and query values can be case-sensitive.
    if kind in {"conference", "online", "standard"}:
        text = _REFERENCE_NUMBER_PREFIX.sub("", original, count=1)
        text = re.sub(r"\s*\n\s*", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
        authors = str(getattr(entry, "authors", "") or "").strip(" .。")
        title = str(getattr(entry, "title", "") or "").strip(" .。")
        if not authors or not title:
            return False
        marker = _TYPE_MARKERS[kind]
        header = re.match(re.escape(authors) + r"\s*[.。]\s*" + re.escape(title)
                          + r"\s*\[\s*" + re.escape(marker) + r"\s*\]", text, re.I)
        if not header:
            return False
        parsed = _parse_pasted_reference(f"author. title[{marker}]{text[header.end():]}")
        if parsed is None or parsed[0] != kind:
            return False
        return all(str(getattr(entry, field, "") or "").strip() == value
                   for field, value in parsed[1].items() if field not in {"authors", "title"})

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
    if stored is not None and legacy is not None and stored != legacy and not getattr(block, "reference_number_managed", False):
        raise ReferenceNumberError(f"参考文献 [{stored}] 的旧文本编号为 [{legacy}]，请核对正文引用后修正")
    return stored or legacy


def reference_number_for(block: Any, position: int) -> int:
    """Use a stored/legacy citation number; position is only a legacy fallback."""
    return _explicit_number(block) or position


def validate_reference_numbers(project: Any) -> None:
    """Reject ambiguous ownership and, when configured, nonconsecutive output."""
    _reconcile_legacy_reference_numbers(project, project.model_copy(deep=True))
    if uses_sequential_reference_numbering(project):
        blocks = [block for block in _reference_blocks(project) if reference_block_text(block).strip()]
        numbers = [reference_number_for(block, index) for index, block in enumerate(blocks, 1)]
        if numbers != list(range(1, len(numbers) + 1)):
            locations = possible_numeric_citation_locations(project)
            if locations:
                raise ReferenceNumberError("参考文献编号不能安全重排；请检查 " + "、".join(locations[:5])
                    + " 中可能受影响的引用，修正后重新保存参考文献。")
            raise ReferenceNumberError("参考文献编号不连续，请重新保存参考文献后再导出。")
        missing = missing_numeric_citation_locations(project, set(numbers))
        if missing:
            raise ReferenceNumberError("正文中存在无法对应参考文献的数字标记，请检查 " + "、".join(missing[:5]) + " 后再导出。")


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


def _reconcile_legacy_reference_numbers(previous: Any, updated: Any) -> None:
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


def uses_sequential_reference_numbering(project: Any) -> bool:
    """Use the same narrowly scoped legacy rule mapping as requirement checks."""
    from app.services.requirement_check import _normalize_legacy_1101_item

    snapshot = getattr(project, "requirement_snapshot", None) or {}
    items = snapshot.get("resolved_items", []) if isinstance(snapshot, dict) else []
    normalized = (_normalize_legacy_1101_item(item) for item in items if isinstance(item, dict))
    return any(item.get("checker") == "references.list_facts"
               and isinstance(item.get("params"), dict)
               and item["params"].get("numbering") == "sequential" for item in normalized)


_POSSIBLE_NUMERIC_CITATION = re.compile(r"[\[【]\s*\d+(?:\s*[,，;；、\-–—~～]\s*\d+)*\s*[\]】]")
_SUPERSCRIPT_DIGITS = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_MEASURED_LENGTH_UNIT = re.compile(r"(?<![A-Za-z0-9_.])(?:\d+(?:\.\d+)?|\.\d+)\s*(?:km|cm|mm|m)\s*$")
_MATH_VARIABLE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-zα-ωΑ-Ω]\s*$")
_MATH_CONTEXT_BEFORE = re.compile(r"(?:计算|变量|函数|表达式|公式|方程|求解|解得|代入|设|令|[=+*/×÷−(（])\s*$")
_MATH_CONTEXT_AFTER = re.compile(r"^\s*[=+*/×÷−]")


def _numeric_citation_evidence(project: Any, protected_numbers: set[int] | None = None) -> list[tuple[str, set[int] | Literal["opaque"] | None]]:
    """Inspect editable text; bitmap pixels and other opaque payloads are outside this scan."""
    evidence = []
    if protected_numbers is None:
        protected_numbers = {int(number) for number in (getattr(project, "reference_number_owners", {}) or {})
                             if isinstance(number, str) and number.isdigit()}

    def numbers(expression: str, location: str) -> None:
        expression = unicodedata.normalize("NFKC", expression)
        values = {int(item) for item in re.findall(r"\d+", expression)}
        if not values:
            return
        for start, end in re.findall(r"(\d+)\s*[-–—~～]\s*(\d+)", expression):
            left, right = int(start), int(end)
            if right < left or right - left > 1000:
                evidence.append((location, None))
                return
            values.update(range(left, right + 1))
        evidence.append((location, values))

    def mathematical_exponent(before: str, exponent: str, after: str) -> bool:
        digits = unicodedata.normalize("NFKC", exponent)
        if not digits.isdigit():
            return False
        if digits in {"2", "3"} and _MEASURED_LENGTH_UNIT.search(before):
            return int(digits) not in protected_numbers
        variable = _MATH_VARIABLE.search(before)
        if variable is None:
            return False
        prefix = before[:variable.start()]
        mathematical_context = (_MATH_CONTEXT_BEFORE.search(prefix) or _MATH_CONTEXT_AFTER.match(after)
                                or (not prefix.strip() and not after.strip()))
        # A number that already belonged to a reference must not silently change owners.
        return bool(mathematical_context and int(digits) not in protected_numbers)

    def examine(text: str, location: str) -> None:
        original = str(text or "")
        normalized = unicodedata.normalize("NFKC", original)
        for match in _POSSIBLE_NUMERIC_CITATION.finditer(normalized):
            numbers(match.group()[1:-1], location)
        for match in _SUPERSCRIPT_DIGITS.finditer(original):
            if not mathematical_exponent(original[:match.start()], match.group(), original[match.end():]):
                numbers(match.group(), location)

    for key, value in (getattr(project, "meta", {}) or {}).items():
        examine(value, f"封面字段 {key}")
    for section in project.sections:
        if str(getattr(section.type, "value", section.type)) == "references" or section.key == "references":
            continue
        examine(section.title, f"章节 {section.key} 标题")
        for block in section.paragraphs:
            location = f"章节 {section.key} 内容块 {block.id}"
            kind = str(getattr(block.type, "value", block.type))
            if kind == "paragraph":
                examine(block.text, location)
                if block.inline_runs:
                    if "".join(run.text for run in block.inline_runs) != block.text:
                        evidence.append((location + "（行内文本不一致）", None))
                    superscript = ""
                    super_start = 0
                    position = 0
                    for run in block.inline_runs:
                        if (run.vert_align if run.vert_align is not None else block.vert_align) == "superscript":
                            if not superscript:
                                super_start = position
                            superscript += run.text
                        elif superscript:
                            if not mathematical_exponent(block.text[:super_start], superscript, block.text[position:]):
                                numbers(superscript, location + "（上标数字）")
                            superscript = ""
                        position += len(run.text)
                    if superscript and not mathematical_exponent(block.text[:super_start], superscript, ""):
                        numbers(superscript, location + "（上标数字）")
                elif block.vert_align == "superscript":
                    numbers(block.text, location + "（上标数字）")
            elif kind == "image" and block.image:
                examine(block.image.caption, location + " 图题")
            elif kind == "table" and block.table:
                examine(block.table.caption, location + " 表题")
                for row, cells in enumerate(block.table.cells):
                    for column, text in enumerate(cells):
                        examine(text, f"{location} 表格第 {row + 1} 行第 {column + 1} 列")
                if block.table.source_xml:
                    # Opaque content prevents safe renumbering, but it does not
                    # establish that an existing citation is missing.
                    evidence.append((location + "（原始表格结构待核验）", "opaque"))
            elif kind == "equation":
                evidence.append((location + "（公式内容待核验）", "opaque"))
            else:
                evidence.append((location + "（内容类型待核验）", None))
    return evidence


def possible_numeric_citation_locations(project: Any) -> list[str]:
    return list(dict.fromkeys(location for location, _ in _numeric_citation_evidence(project)))


def missing_numeric_citation_locations(project: Any, known_numbers: set[int]) -> list[str]:
    return list(dict.fromkeys(location for location, numbers in _numeric_citation_evidence(project)
                              if numbers != "opaque" and (numbers is None or not numbers.issubset(known_numbers))))


def reconcile_reference_numbers(previous: Any, updated: Any) -> None:
    if not uses_sequential_reference_numbering(updated):
        _reconcile_legacy_reference_numbers(previous, updated)
        return
    canonical = previous.model_copy(deep=True)
    _reconcile_legacy_reference_numbers(previous, canonical)
    old_blocks = {block.id: block for block in _reference_blocks(canonical)}
    blocks = _reference_blocks(updated)
    if len({block.id for block in blocks}) != len(blocks):
        raise ReferenceNumberError("参考文献存在重复条目标识，请核对后再保存")
    for block in blocks:
        old = old_blocks.get(block.id)
        if old is not None and block.reference_number is not None and block.reference_number != old.reference_number:
            raise ReferenceNumberError(f"参考文献 {block.id} 的编号被修改，请刷新后重试")
        if old is None and block.reference_number is not None:
            raise ReferenceNumberError(f"新参考文献 {block.id} 的编号由系统分配，请刷新后重试")
    active = [block for block in blocks if reference_block_text(block).strip()]
    protected_numbers = {int(number) for number in canonical.reference_number_owners}
    if _numeric_citation_evidence(updated, protected_numbers):
        _reconcile_legacy_reference_numbers(canonical, updated)
        return
    updated.reference_number_owners = {}
    for index, block in enumerate(active, 1):
        block.reference_number = index
        block.reference_number_managed = True
        updated.reference_number_owners[str(index)] = block.id
    for block in blocks:
        if block not in active:
            block.reference_number = None
            block.reference_number_managed = False
