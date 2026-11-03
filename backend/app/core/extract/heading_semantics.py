"""Conservative recognition of explicitly numbered Word headings."""
from __future__ import annotations

import re

_THIRD_LEVEL = re.compile(r'^\s*\d+\.\d+\.\d+(?![\d.])(?:\s+|(?=[^\W\d_]))\S', re.UNICODE)
_NUMBERED = re.compile(r'^\s*\d+(?:\.\d+){1,2}(?![\d.])\s*(\S.*)$')


def semantic_heading_level(text: str, declared_level: int) -> int:
    """Correct a styled heading with an explicit three-component chapter number.

    A free-standing decimal in ordinary prose does not itself authorize a
    style correction; callers must already have identified a heading.
    """
    if declared_level in (1, 2, 3) and len(text.strip()) < 90 and _THIRD_LEVEL.match(text):
        return 3
    return declared_level


def plausible_unstyled_numbered_heading(text: str) -> bool:
    """Reject obvious decimal measurements and sentences as unstyled titles."""
    text = text.strip()
    match = _NUMBERED.match(text)
    if not match or len(text) > 90 or text.endswith(('。', '！', '？', '；', ';')):
        return False
    return not match.group(1).startswith(('℃', '℉', '°', '%', '％'))
