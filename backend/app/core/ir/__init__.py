"""PaperGuard 中间表示 (Intermediate Representation)。

所有 dataclass 都是 frozen=True（不可变），便于 diff/审计/redline。

设计参考 docs/design-proposal.md §3.1（v1.1 修订：点分属性名）。
"""

from app.core.ir.doc import Document, DocumentMetadata
from app.core.ir.finding import Finding, Fix, Location, Severity
from app.core.ir.paragraph import Paragraph
from app.core.ir.run import Run
from app.core.ir.section import Section

__all__ = [
    "Document",
    "DocumentMetadata",
    "Finding",
    "Fix",
    "Location",
    "Paragraph",
    "Run",
    "Section",
    "Severity",
]
