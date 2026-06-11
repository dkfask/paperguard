"""HTML Reporter — Finding[] -> escaped standalone HTML document."""

from __future__ import annotations

from collections import Counter

from jinja2 import Environment, select_autoescape

from app.core.ir import Finding


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>PaperGuard Report</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; }
    h1 { margin-bottom: 4px; }
    .summary { margin: 16px 0 24px; display: flex; gap: 12px; flex-wrap: wrap; }
    .pill { border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 10px; background: #f9fafb; }
    .finding { border: 1px solid #d1d5db; border-radius: 6px; padding: 14px; margin-bottom: 12px; }
    .error { border-left: 4px solid #dc2626; }
    .warning { border-left: 4px solid #d97706; }
    .info { border-left: 4px solid #2563eb; }
    .meta { color: #4b5563; font-size: 13px; }
    code { background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }
    pre { background: #f3f4f6; padding: 10px; border-radius: 6px; overflow-x: auto; }
  </style>
</head>
<body>
  <h1>PaperGuard Report</h1>
  <p class="meta">{{ total }} findings</p>
  <section class="summary" aria-label="Summary">
    <span class="pill">Error: {{ counts.error }}</span>
    <span class="pill">Warning: {{ counts.warning }}</span>
    <span class="pill">Info: {{ counts.info }}</span>
  </section>
  {% if findings %}
    <section aria-label="Findings">
    {% for f in findings %}
      <article class="finding {{ f.severity.value }}">
        <h2>{{ f.rule_id }} <span class="meta">({{ f.severity.value }})</span></h2>
        <p>{{ f.message }}</p>
        <p class="meta">
          Location:
          {% if f.location.paragraph_index is not none %}
            paragraph {{ f.location.paragraph_index }}
          {% else %}
            document
          {% endif %}
        </p>
        {% if f.location.text_snippet %}
          <pre>{{ f.location.text_snippet }}</pre>
        {% endif %}
        {% if f.fix %}
          <p class="meta">Fix:
            <code>{{ f.fix.action }} {{ f.fix.target }} = {{ f.fix.value }}</code>
          </p>
        {% endif %}
      </article>
    {% endfor %}
    </section>
  {% else %}
    <p>No issues found.</p>
  {% endif %}
</body>
</html>"""


class HTMLReporter:
    """Render findings as escaped, self-contained HTML."""

    def __init__(self) -> None:
        self._env = Environment(
            autoescape=select_autoescape(default=True, default_for_string=True),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._template = self._env.from_string(_TEMPLATE)

    def report(self, findings: list[Finding]) -> str:
        counts = Counter(f.severity.value for f in findings)
        return self._template.render(
            findings=findings,
            total=len(findings),
            counts={
                "error": counts.get("error", 0),
                "warning": counts.get("warning", 0),
                "info": counts.get("info", 0),
            },
        )


__all__ = ["HTMLReporter"]
