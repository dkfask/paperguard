"""PaperGuard Writers — IR → docx 写回 + fix 应用."""

from app.core.writers.fix_applier import apply_fix, apply_all_fixes
from app.core.writers.docx_writer import write_ir_to_docx

__all__ = ["apply_fix", "apply_all_fixes", "write_ir_to_docx"]
