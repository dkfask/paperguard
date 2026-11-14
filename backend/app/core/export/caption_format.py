"""Apply a configured number/title gap without losing inline emphasis."""

import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def set_one_character_gap_width(run):
    """Override inherited character scaling and tracking on the gap run."""
    rpr = run.get_or_add_rPr()
    for name, value in (("w:w", "100"), ("w:spacing", "0")):
        for existing in rpr.findall(qn(name)):
            rpr.remove(existing)
        node = OxmlElement(name)
        node.set(qn("w:val"), value)
        rpr.append(node)


def has_one_character_gap_width(paragraph):
    """Inspect the actual number/title gap run, including its direct geometry."""
    runs = list(paragraph.iter(qn("w:r")))
    texts = ["".join(node.text or "" for node in run.iter(qn("w:t"))) for run in runs]
    match = re.match(r"^\s*[图表]\s*\d+(?:[.\-]\d+)*(\u3000)(?=\S)", "".join(texts))
    if not match:
        return False
    position = match.start(1)
    offset = 0
    for run, text in zip(runs, texts):
        if offset <= position < offset + len(text):
            properties = run.findall(qn("w:rPr"))
            if text != "\u3000" or len(properties) != 1:
                return False
            for name, value in (("w:w", "100"), ("w:spacing", "0")):
                nodes = properties[0].findall(qn(name))
                if (len(nodes) != 1 or nodes[0].get(qn("w:val")) != value
                        or set(nodes[0].attrib) != {qn("w:val")} or len(nodes[0])):
                    return False
            return True
        offset += len(text)
    return False


def caption_with_one_character_gap(text, runs):
    match = re.match(r"^(\s*[图表]\s*\d+(?:[.\-]\d+)*)(\s*)(?=\S)", text)
    if not match:
        return text, runs
    start, end = match.span(2)
    result = text[:start] + "\u3000" + text[end:]
    if not runs or "".join(run.text for run in runs) != text:
        return result, runs
    mapped = []
    offset = 0
    inserted = False
    for run in runs:
        run_end = offset + len(run.text)
        left = max(0, min(len(run.text), start - offset))
        right = max(0, min(len(run.text), end - offset))
        if run.text[:left]:
            mapped.append(run.model_copy(update={"text": run.text[:left]}))
        if not inserted and offset <= start <= run_end:
            mapped.append(run.model_copy(update={"text": "\u3000"}))
            inserted = True
        if run.text[right:]:
            mapped.append(run.model_copy(update={"text": run.text[right:]}))
        offset = run_end
    return result, mapped
