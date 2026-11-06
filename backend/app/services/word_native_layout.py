"""Local, read-only Word pagination probe; it does not authorize delivery."""

from __future__ import annotations

import fcntl
import hashlib
import io
import logging
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from lxml import etree

from app.core.extract.xml_utils import NS, qn, safe_xml_parser
from app.services.source_template_safety import audit_generated_package


LOGGER = logging.getLogger("paperguard.word_layout")


class WordNativeLayoutError(RuntimeError):
    """The local Word measurement could not be trusted."""


_WORD_SCRIPT = r'''
on run argv
    set inputPath to item 1 of argv
    set documentName to item 2 of argv
    set bookmarkNames to items 3 thru -1 of argv
    set outputRows to {}
    with timeout of 80 seconds
        tell application "Microsoft Word"
            if (count documents) is not 0 then error "PAPERGUARD_WORD_BUSY"
            try
                open (POSIX file inputPath) read only true add to recent files false
                set docRef to document documentName
                if (posix full name of docRef) is not inputPath then error "PAPERGUARD_WORD_PATH"
                set end of outputRows to "V|" & (version as text)
                repeat with passNumber from 1 to 2
                    repaginate docRef
                    set totalPages to compute statistics docRef statistic statistic pages
                    set end of outputRows to "T|" & passNumber & "|" & totalPages
                    repeat with bookmarkName in bookmarkNames
                        set bookmarkName to contents of bookmarkName
                        set targetRange to text object of bookmark bookmarkName of docRef
                        set physicalPage to get range information targetRange information type active end page number
                        set logicalPage to get range information targetRange information type active end adjusted page number
                        set end of outputRows to "P|" & passNumber & "|" & bookmarkName & "|" & physicalPage & "|" & logicalPage
                    end repeat
                end repeat
                close docRef saving no
            on error errorText number errorNumber
                try
                    set closeRef to document documentName
                    if (posix full name of closeRef) is inputPath then close closeRef saving no
                end try
                error errorText number errorNumber
            end try
        end tell
    end timeout
    set text item delimiters of AppleScript to linefeed
    return outputRows as text
end run
'''

# Separate from the legacy pagination script: existing signed receipts remain
# verifiable when the optional row-location probe changes.
_WORD_TABLE_ROWS_SCRIPT = r'''
on run argv
    set inputPath to item 1 of argv
    set documentName to item 2 of argv
    set tableBookmarks to items 3 thru -1 of argv
    set outputRows to {}
    with timeout of 80 seconds
        tell application "Microsoft Word"
            if (count documents) is not 0 then error "PAPERGUARD_WORD_BUSY"
            try
                open (POSIX file inputPath) read only true add to recent files false
                set docRef to document documentName
                if (posix full name of docRef) is not inputPath then error "PAPERGUARD_WORD_PATH"
                set end of outputRows to "V|" & (version as text)
                repeat with passNumber from 1 to 2
                    repaginate docRef
                    set totalPages to compute statistics docRef statistic statistic pages
                    set end of outputRows to "T|" & passNumber & "|" & totalPages
                    repeat with bookmarkName in tableBookmarks
                        set bookmarkName to contents of bookmarkName
                        set targetRange to text object of bookmark bookmarkName of docRef
                        if (count tables of targetRange) is not 1 then error "PAPERGUARD_TABLE_COUNT"
                        set targetTable to table 1 of targetRange
                        set rowCount to count rows of targetTable
                        set end of outputRows to "C|" & passNumber & "|" & bookmarkName & "|" & rowCount
                        repeat with rowNumber from 1 to rowCount
                            set rowRange to text object of row rowNumber of targetTable
                            set firstPosition to start of content of rowRange
                            set lastPosition to end of content of rowRange
                            if lastPosition is not greater than firstPosition then error "PAPERGUARD_ROW_RANGE"
                            set firstRange to create range docRef start firstPosition end firstPosition
                            set lastRange to create range docRef start (lastPosition - 1) end (lastPosition - 1)
                            set firstPage to get range information firstRange information type active end page number
                            set lastPage to get range information lastRange information type active end page number
                            set end of outputRows to "R|" & passNumber & "|" & bookmarkName & "|" & rowNumber & "|" & firstPage & "|" & lastPage
                        end repeat
                    end repeat
                end repeat
                close docRef saving no
            on error errorText number errorNumber
                try
                    set closeRef to document documentName
                    if (posix full name of closeRef) is inputPath then close closeRef saving no
                end try
                error errorText number errorNumber
            end try
        end tell
    end timeout
    set text item delimiters of AppleScript to linefeed
    return outputRows as text
end run
'''

# This separate probe leaves existing pagination receipt versions untouched.
# It reports only counts, not manuscript text, for pages preceding the first
# configured content heading.  Offsets are searched in Word's main story so
# physical pages introduced by section breaks can be observed as empty.
_WORD_COVER_SCRIPT = r'''
on firstPositionForPage(docRef, limitPosition, wantedPage)
    set lowPosition to 0
    set highPosition to limitPosition
    tell application "Microsoft Word"
        repeat while lowPosition < highPosition
            set middlePosition to (lowPosition + highPosition) div 2
            set pointRange to create range docRef start middlePosition end middlePosition
            set actualPage to (get range information pointRange information type active end page number) as integer
            if actualPage < wantedPage then
                set lowPosition to middlePosition + 1
            else
                set highPosition to middlePosition
            end if
        end repeat
    end tell
    return lowPosition
end firstPositionForPage

on hasReadableCharacter(valueText)
    repeat with itemCharacter in characters of valueText
        set characterCode to id of itemCharacter
        if characterCode > 32 and characterCode is not in {160, 5760, 6158, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8203, 8204, 8205, 8206, 8207, 8232, 8233, 8234, 8235, 8236, 8237, 8238, 8239, 8287, 8288, 12288, 65279, 65532} then return 1
    end repeat
    return 0
end hasReadableCharacter

-- A text range is only a conservative candidate for visible cover content.
-- Mixed/unsupported formatting is deliberately left unresolved. Shapes and
-- tables alone do not establish that anything was actually rendered.
on hasPlainTextCandidate(docRef, pageRange, firstPosition, lastPosition)
    tell application "Microsoft Word"
        repeat with paragraphNumber from 1 to (count paragraphs of pageRange)
            set paraRange to text object of paragraph paragraphNumber of pageRange
            set partStart to start of content of paraRange
            set partEnd to end of content of paraRange
            if partStart < firstPosition then set partStart to firstPosition
            if partEnd > lastPosition then set partEnd to lastPosition
            if partEnd > partStart then
                set partRange to create range docRef start partStart end partEnd
                if my hasReadableCharacter(content of partRange) is 1 then
                    try
                        set fontRef to font object of partRange
                        set fontHidden to hidden of fontRef
                        set fontSize to font size of fontRef
                        set fontColor to color of fontRef
                        set backgroundName to (background pattern color index of shading of partRange) as text
                        set highlightName to (highlight color index of partRange) as text
                        if fontHidden is false and fontSize > 0 and class of fontColor is list and (count fontColor) is 3 then
                            set redValue to item 1 of fontColor
                            set greenValue to item 2 of fontColor
                            set blueValue to item 3 of fontColor
                            if redValue is greater than or equal to 0 and redValue is less than or equal to 65535 and greenValue is greater than or equal to 0 and greenValue is less than or equal to 65535 and blueValue is greater than or equal to 0 and blueValue is less than or equal to 65535 then
                                if (redValue < 57344 or greenValue < 57344 or blueValue < 57344) and backgroundName is in {"no highlight", "white"} and highlightName is in {"no highlight", "white"} then return 1
                            end if
                        end if
                    end try
                end if
            end if
        end repeat
    end tell
    return 0
end hasPlainTextCandidate

on run argv
    set inputPath to item 1 of argv
    set documentName to item 2 of argv
    set boundaryName to item 3 of argv
    set outputRows to {}
    with timeout of 140 seconds
        tell application "Microsoft Word"
            if (count documents) is not 0 then error "PAPERGUARD_WORD_BUSY"
            try
                open (POSIX file inputPath) read only true add to recent files false
                set docRef to document documentName
                if (posix full name of docRef) is not inputPath then error "PAPERGUARD_WORD_PATH"
                set end of outputRows to "V|" & (version as text)
                repeat with passNumber from 1 to 2
                    repaginate docRef
                    set totalPages to compute statistics docRef statistic statistic pages
                    set boundaryRange to text object of bookmark boundaryName of docRef
                    set boundaryPosition to start of content of boundaryRange
                    set boundaryPoint to create range docRef start boundaryPosition end boundaryPosition
                    set boundaryPage to (get range information boundaryPoint information type active end page number) as integer
                    set boundaryEndPage to (get range information boundaryRange information type active end page number) as integer
                    if boundaryPage is not boundaryEndPage then error "PAPERGUARD_COVER_HEADING_SPAN"
                    if boundaryPosition > 1000000 or boundaryPage > 64 or boundaryPage < 1 then error "PAPERGUARD_COVER_BUDGET"
                    set end of outputRows to "T|" & passNumber & "|" & totalPages
                    set end of outputRows to "B|" & passNumber & "|" & boundaryPage & "|" & boundaryPosition
                    set shapeCounts to {}
                    repeat boundaryPage times
                        set end of shapeCounts to 0
                    end repeat
                    repeat with shapeNumber from 1 to (count shapes of docRef)
                        set shapeRef to shape shapeNumber of docRef
                        set shapeRange to anchor of shapeRef
                        if (start of content of shapeRange) < boundaryPosition and (visible of shapeRef) then
                            set shapePage to (get range information shapeRange information type active end page number) as integer
                            if shapePage < 1 or shapePage > boundaryPage then error "PAPERGUARD_COVER_SHAPE"
                            set item shapePage of shapeCounts to (item shapePage of shapeCounts) + 1
                        end if
                    end repeat
                    repeat with pageNumber from 1 to boundaryPage
                        set firstPosition to my firstPositionForPage(docRef, boundaryPosition, pageNumber)
                        set lastPosition to my firstPositionForPage(docRef, boundaryPosition, pageNumber + 1)
                        set textPresent to 0
                        set plainTextCandidate to 0
                        set inlineCount to 0
                        set tableCount to 0
                        if lastPosition > firstPosition then
                            set pageRange to create range docRef start firstPosition end lastPosition
                            set textPresent to my hasReadableCharacter(content of pageRange)
                            if textPresent is 1 then set plainTextCandidate to my hasPlainTextCandidate(docRef, pageRange, firstPosition, lastPosition)
                            set inlineCount to count inline shapes of pageRange
                            set tableCount to count tables of pageRange
                        end if
                        set shapeCount to item pageNumber of shapeCounts
                        set end of outputRows to "P|" & passNumber & "|" & pageNumber & "|" & textPresent & "|" & plainTextCandidate & "|" & inlineCount & "|" & tableCount & "|" & shapeCount
                    end repeat
                end repeat
                close docRef saving no
            on error errorText number errorNumber
                try
                    set closeRef to document documentName
                    if (posix full name of closeRef) is inputPath then close closeRef saving no
                end try
                error errorText number errorNumber
            end try
        end tell
    end timeout
    set text item delimiters of AppleScript to linefeed
    return outputRows as text
end run
'''

# Word writes the refreshed directory only to a private temporary copy.  The
# caller must independently validate and copy out the directory rows; the rest
# of Word's saved package is never used as a delivery artifact.
_WORD_TOC_SCRIPT = r'''
on run argv
    set inputPath to item 1 of argv
    with timeout of 80 seconds
        tell application "Microsoft Word"
            if (count documents) is not 0 then error "PAPERGUARD_WORD_BUSY"
            try
                open (POSIX file inputPath) read only false add to recent files false
                set docRef to active document
                if (posix full name of docRef) is not inputPath then error "PAPERGUARD_WORD_PATH"
                if (count tables of contents of docRef) is not 1 then error "PAPERGUARD_TOC_COUNT"
                update table of contents 1 of docRef
                repaginate docRef
                save docRef
                close docRef saving no
                return "OK"
            on error errorText number errorNumber
                try
                    if (count documents) is not 0 then
                        set closeRef to active document
                        if (posix full name of closeRef) is inputPath then close closeRef saving no
                    end if
                end try
                error errorText number errorNumber
            end try
        end tell
    end timeout
end run
'''

_CLEANUP_SCRIPT = r'''
on run argv
    set inputPath to item 1 of argv
    if application "Microsoft Word" is not running then return "ABSENT"
    with timeout of 5 seconds
        tell application "Microsoft Word"
            repeat with documentIndex from (count documents) to 1 by -1
                set docRef to document documentIndex
                try
                    set docPath to posix full name of docRef
                on error
                    set docPath to ""
                end try
                if docPath is inputPath then
                    close docRef saving no
                    return "CLOSED"
                end if
            end repeat
        end tell
    end timeout
    return "ABSENT"
end run
'''

_BOOKMARK = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}\Z")
_PAGEREF = re.compile(r"^\s*PAGEREF\s+([A-Za-z][A-Za-z0-9_]{0,39})(?=\s|$)", re.I)
_MAX_TARGETS = 256
_MAX_MEASURED_BOOKMARKS = 4096
_MAX_MEASURED_TABLES = 256
_MAX_MEASURED_TABLE_ROWS = 4096


def _cleanup_word_temporary_document(source: Path) -> bool:
    """Best-effort close of our exact temporary copy, never other documents."""
    if (not source.is_absolute() or source.name != "paperguard-input.docx"
            or not source.parent.name.startswith("paperguard-word-")):
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-", str(source)], input=_CLEANUP_SCRIPT,
            capture_output=True, text=True, timeout=7, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() in {"CLOSED", "ABSENT"}


def _validate_extra_bookmarks(data: bytes, names: list[str]) -> None:
    """Require each requested generated bookmark to exist exactly once."""
    if (len(names) > _MAX_MEASURED_BOOKMARKS
            or any(not isinstance(name, str) or not _BOOKMARK.fullmatch(name) for name in names)
            or len({name.casefold() for name in names}) != len(names)):
        raise WordNativeLayoutError("待测布局书签无效、重复或超出验证上限")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"), safe_xml_parser())
    starts = Counter(name.casefold() for name in root.xpath(".//w:bookmarkStart/@w:name", namespaces=NS))
    if any(starts[name.casefold()] != 1 for name in names):
        raise WordNativeLayoutError("待测布局书签缺失或不唯一")


def _directory_references(data: bytes) -> list[tuple[str, str]]:
    """Read cached PAGEREF fields without evaluating them."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"), safe_xml_parser())
    starts = Counter(name.casefold() for name in root.xpath(".//w:bookmarkStart/@w:name", namespaces=NS))
    rows: list[tuple[str, str]] = []

    def add(instruction: str, cached: str) -> None:
        if not re.match(r"^\s*PAGEREF(?:\s|$)", instruction, re.I):
            return
        match = _PAGEREF.match(instruction)
        if not match or starts[match.group(1).casefold()] != 1:
            raise WordNativeLayoutError("目录页码引用的书签缺失或不唯一")
        rows.append((match.group(1), cached.strip()))

    def pageref_in_progress(stack: list[dict]) -> bool:
        return any(re.match(r"^\s*PAGEREF(?:\s|$)", "".join(field["instruction"]), re.I)
                   for field in stack)

    for paragraph in root.xpath(".//w:p", namespaces=NS):
        stack: list[dict] = []
        for node in paragraph.iter():
            if node.tag == qn("w:fldSimple"):
                instruction = node.get(qn("w:instr"), "")
                if pageref_in_progress(stack):
                    parent = stack[-1]
                    parent_ref = _PAGEREF.match("".join(parent["instruction"]))
                    child_ref = _PAGEREF.match(instruction)
                    if (not parent["separated"] or not parent_ref or not child_ref
                            or parent_ref.group(1).casefold() != child_ref.group(1).casefold()):
                        raise WordNativeLayoutError("目录页码引用包含不支持的嵌套域")
                    continue
                add(instruction, "".join(node.xpath(".//w:t/text()", namespaces=NS)))
            elif node.tag == qn("w:fldChar"):
                kind = node.get(qn("w:fldCharType"), "").lower()
                if kind == "begin":
                    if pageref_in_progress(stack):
                        raise WordNativeLayoutError("目录页码引用包含不支持的嵌套域")
                    stack.append({"instruction": [], "cached": [], "separated": False})
                elif kind == "separate" and stack:
                    stack[-1]["separated"] = True
                elif kind == "end" and stack:
                    field = stack.pop()
                    add("".join(field["instruction"]), "".join(field["cached"]))
            elif node.tag == qn("w:instrText") and stack and not stack[-1]["separated"]:
                stack[-1]["instruction"].append(node.text or "")
            elif node.tag == qn("w:t") and stack and stack[-1]["separated"]:
                stack[-1]["cached"].append(node.text or "")
        if pageref_in_progress(stack):
            raise WordNativeLayoutError("目录页码引用不完整")
    if not rows or len(rows) > _MAX_TARGETS:
        raise WordNativeLayoutError("目录页码引用为空或超出本机验证上限")
    return rows


def _page_value(text: str) -> int | None:
    value = text.strip().upper()
    if re.fullmatch(r"[1-9][0-9]{0,5}", value):
        return int(value)
    if not re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", value) or not value:
        return None
    numbers = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    return sum(-numbers[char] if index + 1 < len(value) and numbers[char] < numbers[value[index + 1]]
               else numbers[char] for index, char in enumerate(value))


def _formatted_page(cached: str, page: int) -> str:
    """Retain the source numbering system when updating a Word measurement."""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise WordNativeLayoutError("Word 页码无效")
    if re.fullmatch(r"[1-9][0-9]{0,5}", cached):
        return str(page)
    if _page_value(cached) is None or page > 3999:
        raise WordNativeLayoutError("目录页码格式无法安全更新")
    remaining = page
    result = ""
    for amount, symbol in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
                           (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                           (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        count, remaining = divmod(remaining, amount)
        result += symbol * count
    return result.lower() if cached.islower() else result


def update_word_directory_cache(data: bytes, measurement: dict) -> bytes:
    """Update simple PAGEREF caches using a measurement of these exact bytes.

    This operation alone does not authorize delivery.  Callers must remeasure
    the result and bind the final document to a separately verified receipt.
    """
    if measurement.get("source_sha256") != hashlib.sha256(data).hexdigest():
        raise WordNativeLayoutError("Word 分页证据与目录文件不匹配")
    expected = _directory_references(data)
    rows = measurement.get("toc")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise WordNativeLayoutError("Word 分页证据不完整")
    names = [name.casefold() for name, _ in expected]
    if len(names) != len(set(names)):
        raise WordNativeLayoutError("目录页码引用重复，无法安全更新")
    for row, (name, cached) in zip(rows, expected):
        if (not isinstance(row, dict) or row.get("bookmark") != name
                or row.get("cached_page") != cached or type(row.get("logical_page")) is not int
                or row["logical_page"] < 1):
            raise WordNativeLayoutError("Word 分页证据与目录字段不匹配")
    if all(_page_value(cached) == row["logical_page"] for row, (_, cached) in zip(rows, expected)):
        return data
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as archive, zipfile.ZipFile(output, "w") as target:
        for item in archive.infolist():
            payload = archive.read(item.filename)
            if item.filename == "word/document.xml":
                root = etree.fromstring(payload, safe_xml_parser())
                fields: dict[str, list] = {}
                for node in root.xpath(".//w:fldSimple[@w:instr]", namespaces=NS):
                    match = _PAGEREF.match(node.get(qn("w:instr"), ""))
                    if match:
                        fields.setdefault(match.group(1).casefold(), []).append(node)
                for row, (name, cached) in zip(rows, expected):
                    if _page_value(cached) == row["logical_page"]:
                        continue
                    targets = fields.get(name.casefold(), [])
                    if len(targets) != 1:
                        raise WordNativeLayoutError("目录页码字段无法唯一定位")
                    text_nodes = targets[0].xpath(".//w:t", namespaces=NS)
                    if len(text_nodes) != 1 or (text_nodes[0].text or "").strip() != cached:
                        raise WordNativeLayoutError("目录页码字段结构无法安全更新")
                    text_nodes[0].text = _formatted_page(cached, row["logical_page"])
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            target.writestr(item, payload)
    return output.getvalue()


def _parse_measurement(output: str, names: list[str]) -> tuple[str, int, dict[str, tuple[int, int]]]:
    version = ""
    totals: dict[int, int] = {}
    passes: dict[int, dict[str, tuple[int, int]]] = {1: {}, 2: {}}
    try:
        for line in output.splitlines():
            parts = line.split("|")
            if len(parts) == 2 and parts[0] == "V" and not version:
                version = parts[1]
            elif len(parts) == 3 and parts[0] == "T":
                number, total = int(parts[1]), int(parts[2])
                if number not in passes or number in totals or total < 1:
                    raise ValueError
                totals[number] = total
            elif len(parts) == 5 and parts[0] == "P":
                number, name = int(parts[1]), parts[2]
                physical, logical = int(parts[3]), int(parts[4])
                if number not in passes or name in passes[number] or physical < 1 or logical < 1:
                    raise ValueError
                passes[number][name] = (physical, logical)
            else:
                raise ValueError
    except ValueError as exc:
        raise WordNativeLayoutError("Word 返回的分页证据格式无效") from exc
    expected = set(names)
    if (not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)
            or set(totals) != {1, 2} or totals[1] != totals[2]
            or set(passes[1]) != expected or passes[1] != passes[2]
            or any(physical > totals[1] for physical, _ in passes[1].values())):
        raise WordNativeLayoutError("Word 分页未收敛或书签定位不完整")
    return version, totals[1], passes[1]


def _run_word(path: Path, names: list[str], timeout: int) -> str:
    if sys.platform != "darwin":
        raise WordNativeLayoutError("本机 Word 页码采集仅支持 macOS")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-", str(path), path.name, *names],
            input=_WORD_SCRIPT, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleaned = _cleanup_word_temporary_document(path)
        message = ("Word 分页超时，请稍后重试" if cleaned else
                   "Word 分页超时，临时文档状态暂未确认，请联系系统团队处理")
        raise WordNativeLayoutError(message) from exc
    except OSError as exc:
        raise WordNativeLayoutError("本机 Word 排版组件不可用") from exc
    if result.returncode:
        if "PAPERGUARD_WORD_BUSY" in result.stderr:
            raise WordNativeLayoutError("Word 正在使用其他文档，请关闭后重试")
        error_code = re.search(r"\((-(?:1712|1708|1728|1743|10000|128|600|609))\)\s*$", result.stderr or "")
        LOGGER.warning("word pagination failed exit_code=%s apple_event_code=%s", result.returncode,
                       error_code.group(1) if error_code else "unknown")
        _cleanup_word_temporary_document(path)
        raise WordNativeLayoutError("Word 未能完成分页或书签定位，请检查本机 Word 后重试")
    return result.stdout


def _parse_cover_measurement(output: str) -> tuple[str, int, int, list[dict]]:
    version = ""
    totals = {}
    boundaries = {}
    pages = {1: [], 2: []}
    try:
        for line in output.splitlines():
            parts = line.split("|")
            if len(parts) == 2 and parts[0] == "V" and not version:
                version = parts[1]
            elif len(parts) == 3 and parts[0] == "T":
                number, total = int(parts[1]), int(parts[2])
                if number not in pages or number in totals or total < 1:
                    raise ValueError
                totals[number] = total
            elif len(parts) == 4 and parts[0] == "B":
                number, page, position = map(int, parts[1:])
                if number not in pages or number in boundaries or not 1 <= page <= 64 or not 0 <= position <= 1000000:
                    raise ValueError
                boundaries[number] = (page, position)
            elif len(parts) == 8 and parts[0] == "P":
                number, page, text_present, plain_text, inline_count, table_count, shape_count = map(int, parts[1:])
                if (number not in pages or page != len(pages[number]) + 1 or text_present not in (0, 1)
                        or plain_text not in (0, 1) or plain_text > text_present
                        or min(inline_count, table_count, shape_count) < 0):
                    raise ValueError
                pages[number].append({"page": page, "text_present": bool(text_present),
                    "plain_text_candidate": bool(plain_text),
                    "inline_shapes": inline_count, "tables": table_count, "floating_shapes": shape_count})
            else:
                raise ValueError
    except (TypeError, ValueError) as exc:
        raise WordNativeLayoutError("Word 返回的封面分页证据格式无效") from exc
    if (not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)
            or set(totals) != {1, 2} or totals[1] != totals[2]
            or set(boundaries) != {1, 2} or boundaries[1] != boundaries[2]
            or boundaries[1][0] > totals[1] or pages[1] != pages[2]
            or len(pages[1]) != boundaries[1][0]):
        raise WordNativeLayoutError("Word 封面分页未收敛或逐页证据不完整")
    return version, totals[1], boundaries[1][0], pages[1]


def _run_word_cover(path: Path, boundary: str, timeout: int) -> str:
    if sys.platform != "darwin":
        raise WordNativeLayoutError("本机 Word 封面分页采集仅支持 macOS")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-", str(path), path.name, boundary],
            input=_WORD_COVER_SCRIPT, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleaned = _cleanup_word_temporary_document(path)
        message = ("Word 封面分页超时，请稍后重试" if cleaned else
                   "Word 封面分页超时，临时文档状态暂未确认，请联系系统团队处理")
        raise WordNativeLayoutError(message) from exc
    except OSError as exc:
        raise WordNativeLayoutError("本机 Word 封面分页组件不可用") from exc
    if result.returncode:
        if "PAPERGUARD_WORD_BUSY" in result.stderr:
            raise WordNativeLayoutError("Word 正在使用其他文档，请关闭后重试")
        error_code = re.search(r"\((-(?:1712|1708|1728|1743|10000|128|600|609))\)\s*$", result.stderr or "")
        LOGGER.warning("word cover pagination failed exit_code=%s apple_event_code=%s", result.returncode,
                       error_code.group(1) if error_code else "unknown")
        _cleanup_word_temporary_document(path)
        raise WordNativeLayoutError("Word 未能完成封面逐页定位，请检查本机 Word 后重试")
    return result.stdout


def _cover_has_plain_text_context(data: bytes, boundary: str) -> bool:
    """Refuse to infer visible text when page background/effects are unknown."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"), safe_xml_parser())
        styles = etree.fromstring(archive.read("word/styles.xml"), safe_xml_parser())
    backgrounds = root.findall(qn("w:background"))
    if len(backgrounds) > 1:
        return False
    if backgrounds:
        background = backgrounds[0]
        if (len(background) or set(background.attrib) != {qn("w:color")}
                or background.get(qn("w:color"), "").upper() != "FFFFFF"):
            return False
    # Word's simple font/color properties need not expose newer text effects.
    # Restrict this claim to cover runs and styles without such extensions.
    word2010 = "http://schemas.microsoft.com/office/word/2010/wordml"
    for item in styles.iter():
        if (isinstance(item.tag, str) and etree.QName(item).namespace == word2010
                and any(parent.tag == qn("w:rPr") for parent in item.iterancestors())):
            return False
    for item in root.iter():
        if item.tag == qn("w:bookmarkStart") and item.get(qn("w:name")) == boundary:
            return True
        if (isinstance(item.tag, str) and etree.QName(item).namespace == word2010
                and any(parent.tag == qn("w:rPr") for parent in item.iterancestors())):
            return False
    return False


def inspect_word_cover(source_path: Path, boundary: str, *, word_documents_dir: Path | None = None,
                       timeout: int = 150) -> dict:
    """Measure main-story content before a source-bound heading, without exposing its text."""
    if timeout < 1 or timeout > 180:
        raise ValueError("timeout must be between 1 and 180 seconds")
    source_path = Path(source_path)
    source = source_path.read_bytes()
    audit_generated_package(source)
    _validate_extra_bookmarks(source, [boundary])
    with _word_workspace(word_documents_dir) as temporary:
        copy = temporary / "paperguard-input.docx"
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(source)
        version, total, boundary_page, pages = _parse_cover_measurement(_run_word_cover(copy, boundary, timeout))
        if copy.read_bytes() != source or source_path.read_bytes() != source:
            raise WordNativeLayoutError("封面分页期间 Word 文件发生变化")
    return {"scope": "word_cover_main_story_pages_only", "source_sha256": hashlib.sha256(source).hexdigest(),
            "word_version": version, "macos_version": platform.mac_ver()[0], "machine": platform.machine(),
            "probe_sha256": hashlib.sha256(_WORD_COVER_SCRIPT.encode()).hexdigest(), "page_count": total,
            "pagination_stable": True, "plain_text_context": _cover_has_plain_text_context(source, boundary),
            "boundary_bookmark": boundary, "boundary_page": boundary_page, "pages": pages}


def _validate_table_targets(data: bytes, targets: list[dict]) -> None:
    """Bind every requested first/last bookmark to one actual DOCX table."""
    if not isinstance(targets, list) or len(targets) > _MAX_MEASURED_TABLES:
        raise WordNativeLayoutError("待测表格超出验证上限")
    names = []
    for item in targets:
        if (not isinstance(item, dict) or set(item) != {"bookmark", "end_bookmark", "row_count"}
                or not isinstance(item["bookmark"], str) or not isinstance(item["end_bookmark"], str)
                or type(item["row_count"]) is not int or not 1 <= item["row_count"] <= 50):
            raise WordNativeLayoutError("待测表格信息无效")
        names.extend((item["bookmark"], item["end_bookmark"]))
    if sum(item["row_count"] for item in targets) > _MAX_MEASURED_TABLE_ROWS:
        raise WordNativeLayoutError("待测表格行数超出验证上限")
    _validate_extra_bookmarks(data, names)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"), safe_xml_parser())
    starts = {node.get(qn("w:name")): node for node in root.xpath(".//w:bookmarkStart", namespaces=NS)}
    tables = set()
    for target in targets:
        first = starts[target["bookmark"]]
        last = starts[target["end_bookmark"]]
        first_tables = first.xpath("ancestor::w:tbl[1]", namespaces=NS)
        last_tables = last.xpath("ancestor::w:tbl[1]", namespaces=NS)
        first_rows = first.xpath("ancestor::w:tr[1]", namespaces=NS)
        last_rows = last.xpath("ancestor::w:tr[1]", namespaces=NS)
        if len(first_tables) != 1 or len(last_tables) != 1 or first_tables[0] is not last_tables[0]:
            raise WordNativeLayoutError("表格书签未指向同一张表格")
        table = first_tables[0]
        rows = table.findall(qn("w:tr"))
        if (table in tables or len(rows) != target["row_count"]
                or first_rows != rows[:1] or last_rows != rows[-1:]):
            raise WordNativeLayoutError("表格行数或边界书签与当前文件不匹配")
        tables.add(table)


def _parse_table_measurement(output: str, targets: list[dict]) -> tuple[str, int, list[dict]]:
    """Accept only complete, ordered and stable row-location observations."""
    expected = {item["bookmark"]: item["row_count"] for item in targets}
    version = ""
    totals: dict[int, int] = {}
    counts: dict[int, dict[str, int]] = {1: {}, 2: {}}
    rows: dict[int, dict[str, list[dict]]] = {1: {}, 2: {}}
    try:
        for line in output.splitlines():
            fields = line.split("|")
            if len(fields) == 2 and fields[0] == "V" and not version:
                version = fields[1]
            elif len(fields) == 3 and fields[0] == "T":
                number, total = int(fields[1]), int(fields[2])
                if number not in totals and number in counts and total > 0:
                    totals[number] = total
                else:
                    raise ValueError
            elif len(fields) == 4 and fields[0] == "C":
                number, name, count = int(fields[1]), fields[2], int(fields[3])
                if number not in counts or name not in expected or name in counts[number] or count != expected[name]:
                    raise ValueError
                counts[number][name] = count
                rows[number][name] = []
            elif len(fields) == 6 and fields[0] == "R":
                number, name = int(fields[1]), fields[2]
                index, first, last = int(fields[3]), int(fields[4]), int(fields[5])
                if (number not in rows or name not in rows[number] or index != len(rows[number][name]) + 1
                        or index > expected[name] or first < 1 or last < first):
                    raise ValueError
                rows[number][name].append({"row": index, "start_page": first, "end_page": last})
            else:
                raise ValueError
    except ValueError as exc:
        raise WordNativeLayoutError("Word 返回的表格分页证据格式无效") from exc
    if (not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)
            or set(totals) != {1, 2} or totals[1] != totals[2]
            or counts[1] != expected or counts[2] != expected or rows[1] != rows[2]):
        raise WordNativeLayoutError("Word 表格分页未收敛或行定位不完整")
    result = []
    for target in targets:
        name = target["bookmark"]
        found = rows[1][name]
        if len(found) != target["row_count"] or any(item["end_page"] > totals[1] for item in found):
            raise WordNativeLayoutError("Word 表格分页行定位不完整或超出页数")
        if any(left["end_page"] > right["start_page"] for left, right in zip(found, found[1:])):
            raise WordNativeLayoutError("Word 表格分页行顺序无效")
        result.append({"bookmark": name, "rows": found})
    return version, totals[1], result


def _run_word_table_rows(path: Path, names: list[str], timeout: int) -> str:
    if sys.platform != "darwin":
        raise WordNativeLayoutError("本机 Word 表格分页采集仅支持 macOS")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-", str(path), path.name, *names],
            input=_WORD_TABLE_ROWS_SCRIPT, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleaned = _cleanup_word_temporary_document(path)
        message = ("Word 表格分页超时，请稍后重试" if cleaned else
                   "Word 表格分页超时，临时文档状态暂未确认，请联系系统团队处理")
        raise WordNativeLayoutError(message) from exc
    except OSError as exc:
        raise WordNativeLayoutError("本机 Word 表格分页组件不可用") from exc
    if result.returncode:
        if "PAPERGUARD_WORD_BUSY" in result.stderr:
            raise WordNativeLayoutError("Word 正在使用其他文档，请关闭后重试")
        _cleanup_word_temporary_document(path)
        raise WordNativeLayoutError("Word 未能完成表格分页定位，请检查本机 Word 后重试")
    return result.stdout


def inspect_word_table_rows(source_path: Path, targets: list[dict], *,
                            word_documents_dir: Path | None = None, timeout: int = 90) -> dict:
    """Locate table-row boundaries; this does not establish content visibility."""
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    if not targets:
        raise ValueError("at least one table is required")
    source_path = Path(source_path)
    source = source_path.read_bytes()
    audit_generated_package(source)
    _validate_table_targets(source, targets)
    with _word_workspace(word_documents_dir) as temporary:
        copy = temporary / "paperguard-input.docx"
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(source)
        raw = _run_word_table_rows(copy, [item["bookmark"] for item in targets], timeout)
        version, total, tables = _parse_table_measurement(raw, targets)
        if copy.read_bytes() != source or source_path.read_bytes() != source:
            raise WordNativeLayoutError("表格分页期间 Word 文件发生变化")
    return {"scope": "word_table_row_locations_only", "source_sha256": hashlib.sha256(source).hexdigest(),
            "word_version": version, "macos_version": platform.mac_ver()[0], "machine": platform.machine(),
            "probe_sha256": hashlib.sha256(_WORD_TABLE_ROWS_SCRIPT.encode()).hexdigest(),
            "page_count": total, "pagination_stable": True, "tables": tables}


def _run_word_toc(path: Path, timeout: int) -> None:
    """Refresh a single TOC in Word without touching any other open document."""
    if sys.platform != "darwin":
        raise WordNativeLayoutError("本机 Word 目录更新仅支持 macOS")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-", str(path)], input=_WORD_TOC_SCRIPT,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleaned = _cleanup_word_temporary_document(path)
        message = ("Word 目录更新超时，请稍后重试" if cleaned else
                   "Word 目录更新超时，临时文档状态暂未确认，请联系系统团队处理")
        raise WordNativeLayoutError(message) from exc
    except OSError as exc:
        raise WordNativeLayoutError("本机 Word 目录组件不可用") from exc
    if result.returncode or result.stdout.strip() != "OK":
        if "PAPERGUARD_WORD_BUSY" in result.stderr:
            raise WordNativeLayoutError("Word 正在使用其他文档，请关闭后重试")
        _cleanup_word_temporary_document(path)
        raise WordNativeLayoutError("Word 未能安全更新唯一目录，请稍后重试")


def refresh_word_toc(source_path: Path, *, word_documents_dir: Path | None = None,
                     timeout: int = 90) -> bytes:
    """Return Word's updated temporary copy; do not change or authorize source."""
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    source_path = Path(source_path)
    source = source_path.read_bytes()
    audit_generated_package(source)
    with _word_workspace(word_documents_dir) as temporary:
        copy = temporary / "paperguard-input.docx"
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(source)
        _run_word_toc(copy, timeout)
        info = copy.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 80 * 1024 * 1024:
            raise WordNativeLayoutError("Word 目录更新结果不是受控的临时文件")
        result = copy.read_bytes()
        audit_generated_package(result)
        if source_path.read_bytes() != source:
            raise WordNativeLayoutError("目录更新期间原 Word 文件发生变化")
    return result


@contextmanager
def _word_workspace(word_documents_dir: Path | None = None):
    """Serialize local Word use and keep temporary files in its sandbox."""
    directory = word_documents_dir or Path.home() / "Library/Containers/com.microsoft.Word/Data/Documents"
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise WordNativeLayoutError("本机 Word 的文档目录不可用")
    lock_path = Path(tempfile.gettempdir()) / f"paperguard-word-layout-{os.getuid()}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(lock_fd)
            raise WordNativeLayoutError("本机 Word 排版锁不可用")
        with os.fdopen(lock_fd, "r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WordNativeLayoutError("另一个 Word 排版任务正在运行") from exc
            with tempfile.TemporaryDirectory(prefix="paperguard-word-", dir=directory) as temporary:
                yield Path(temporary)
    except OSError as exc:
        raise WordNativeLayoutError("本机 Word 排版临时目录不可用") from exc


def inspect_word_pagination(source_path: Path, *, word_documents_dir: Path | None = None,
                            timeout: int = 90, extra_bookmarks: list[str] | None = None) -> dict:
    """Measure a copied DOCX with local Word; never modify or approve the source."""
    if timeout < 1 or timeout > 180:
        raise ValueError("timeout must be between 1 and 180 seconds")
    source_path = Path(source_path)
    source = source_path.read_bytes()
    audit_generated_package(source)
    rows = _directory_references(source)
    extra = list(extra_bookmarks or [])
    _validate_extra_bookmarks(source, extra)
    names = list(dict.fromkeys(name for name, _ in rows))
    by_fold = {name.casefold(): name for name in names}
    for name in extra:
        previous = by_fold.get(name.casefold())
        if previous is not None and previous != name:
            raise WordNativeLayoutError("目录与内容布局书签名称冲突")
        if previous is None:
            by_fold[name.casefold()] = name
            names.append(name)
    if len(names) > _MAX_MEASURED_BOOKMARKS:
        raise WordNativeLayoutError("待测布局书签超出验证上限")
    if any(not _BOOKMARK.fullmatch(name) for name in names):
        raise WordNativeLayoutError("目录书签名称无效")
    with _word_workspace(word_documents_dir) as temporary:
        copy = temporary / "paperguard-input.docx"
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(source)
        version, total, positions = _parse_measurement(_run_word(copy, names, timeout), names)
        if copy.read_bytes() != source or source_path.read_bytes() != source:
            raise WordNativeLayoutError("分页期间 Word 文件发生变化")
    comparisons = [{"bookmark": name, "cached_page": cached, "physical_page": positions[name][0],
                    "logical_page": positions[name][1], "matches": _page_value(cached) == positions[name][1]}
                   for name, cached in rows]
    return {
        "scope": "local_word_pagination_probe_only",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "word_version": version,
        "macos_version": platform.mac_ver()[0],
        "machine": platform.machine(),
        "probe_sha256": hashlib.sha256(_WORD_SCRIPT.encode()).hexdigest(),
        "page_count": total,
        "pagination_stable": True,
        "toc_matches": all(item["matches"] for item in comparisons),
        "toc": comparisons,
        "extra_bookmarks": [
            {"bookmark": name, "physical_page": positions[name][0], "logical_page": positions[name][1]}
            for name in extra
        ],
    }
