"""Resolve DOCX formatting for specification evidence without modifying the file.

This extends the existing property readers. Values retain their unit/kind and
the exact XML locations which contributed them. Unsupported conditional table
styles are reported instead of silently asserting a complete effective style.
"""

from __future__ import annotations

from typing import Any
import math

from lxml import etree

from app.core.extract.para_props import para_props
from app.core.extract.run_props import run_props
from app.core.extract.xml_utils import NS, qn

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def location(element: etree._Element) -> str:
    return element.getroottree().getpath(element)


def on(element: etree._Element | None) -> bool:
    return element is not None and (element.get(qn("w:val")) or "1").lower() not in {"0", "false", "off", "no"}


def number(raw: str | None, divisor: float = 1) -> float | None:
    try:
        value = float(raw) / divisor
        return round(value, 6) if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def measure(raw: str | None, divisor: float, unit: str, kind: str = "") -> dict[str, Any] | None:
    value = number(raw, divisor)
    if value is None:
        return None
    result: dict[str, Any] = {"value": value, "unit": unit}
    if kind:
        result["kind"] = kind
    return result


class EffectiveFormatResolver:
    def __init__(self, styles: etree._Element | None, theme: etree._Element | None = None,
                 settings: etree._Element | None = None):
        self.styles = {
            s.get(qn("w:styleId")): s for s in styles.findall(qn("w:style"))
        } if styles is not None else {}
        self.defaults = styles.find(qn("w:docDefaults")) if styles is not None else None
        self.theme = theme
        lang = settings.find(qn("w:themeFontLang")) if settings is not None else None
        self.theme_language = lang.get(qn("w:eastAsia"), "") if lang is not None else ""
        self.default_style = next((key for key, s in self.styles.items()
                                   if s.get(qn("w:type")) == "paragraph"
                                   and s.get(qn("w:default"), "").lower() in {"1", "true", "on"}), "")
        self.issues: set[str] = set()

    def chain(self, style_id: str) -> list[etree._Element]:
        chain: list[etree._Element] = []
        seen: set[str] = set()
        while style_id:
            if style_id in seen or len(seen) >= 128:
                self.issues.add("style_inheritance_cycle")
                break
            seen.add(style_id)
            style = self.styles.get(style_id)
            if style is None:
                self.issues.add("missing_style_reference")
                break
            chain.append(style)
            base = style.find(qn("w:basedOn"))
            style_id = base.get(qn("w:val"), "") if base is not None else ""
        return list(reversed(chain))

    def style_name(self, style_id: str) -> str:
        style = self.styles.get(style_id)
        name = style.find(qn("w:name")) if style is not None else None
        return name.get(qn("w:val"), "") if name is not None else ""

    def _theme_font(self, token: str, language: str) -> str:
        if self.theme is None:
            return ""
        family = "majorFont" if token.startswith("major") else "minorFont"
        script = "ea" if token.endswith("EastAsia") else "cs" if token.endswith("Bidi") else "latin"
        font = self.theme.find(f".//{{{A_NS}}}{family}/{{{A_NS}}}{script}")
        if font is not None and font.get("typeface"):
            return font.get("typeface", "")
        language = language.lower()
        supplemental = ("Hant" if language.startswith(("zh-tw", "zh-hk", "zh-mo")) else
                        "Hans" if language.startswith("zh") else "Jpan" if language.startswith("ja") else
                        "Hang" if language.startswith("ko") else "")
        for font in self.theme.findall(f".//{{{A_NS}}}{family}/{{{A_NS}}}font"):
            if supplemental and font.get("script") == supplemental:
                return font.get("typeface", "")
        return ""

    def _run(self, target: dict, sources: dict, rpr: etree._Element | None, *, style: bool = False) -> None:
        if rpr is None:
            return
        origin = f"word/styles.xml:{location(rpr)}" if style or rpr.getroottree().getroot().tag == qn("w:styles") else location(rpr)
        raw = run_props(rpr)
        fonts = rpr.find(qn("w:rFonts"))
        lang = rpr.find(qn("w:lang"))
        if lang is not None:
            target["language.eastAsia"] = lang.get(qn("w:eastAsia"), target.get("language.eastAsia", ""))
        if fonts is not None:
            for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
                literal = fonts.get(qn(f"w:{slot}"))
                theme = fonts.get(qn("w:cstheme" if slot == "cs" else f"w:{slot}Theme"))
                prop = f"font.{slot}"
                if literal is None and theme is None:
                    continue
                # A theme reference on this layer takes precedence over its literal fallback.
                value = self._theme_font(theme, target.get("language.eastAsia") or self.theme_language) if theme else literal
                target[prop] = value or {"unresolved_theme": theme}
                sources[prop] = [origin]
                if theme and not value:
                    self.issues.add("unresolved_theme_font")
        for prop, value in raw.items():
            if prop in {"font.ascii", "font.hAnsi", "font.eastAsia", "font.cs"}:
                continue
            if prop in {"font.bold", "font.italic"}:
                value = "true" if on(rpr.find(qn("w:b" if prop == "font.bold" else "w:i"))) else "false"
                if style:
                    # Style b/i are toggles; an explicit false in a style leaves the inherited value.
                    if value == "true":
                        target[prop] = not target.get(prop, False)
                else:
                    target[prop] = value == "true"
            elif prop == "font.size_half_points":
                prop = "font.size"
                target[prop] = measure(value, 2, "pt")
            else:
                target[prop] = value
            sources[prop] = [*sources.get(prop, []), origin] if style else [origin]
        for name, prop in (("vertAlign", "font.vertical_alignment"), ("highlight", "font.highlight")):
            child = rpr.find(qn(f"w:{name}"))
            if child is not None:
                target[prop] = child.get(qn("w:val"), "")
                sources[prop] = [origin]
        underline = rpr.find(qn("w:u"))
        if underline is not None:
            target["font.underline"] = underline.get(qn("w:val"), "single")
            sources["font.underline"] = [origin]

    def paragraph(self, paragraph: etree._Element, *, include_whitespace: bool = False) -> tuple[dict, dict, list[dict], str]:
        ppr = paragraph.find(qn("w:pPr"))
        sid = ppr.find(qn("w:pStyle")) if ppr is not None else None
        style_id = sid.get(qn("w:val"), "") if sid is not None else self.default_style
        rvalues: dict = {}
        rsources: dict = {}
        praw: dict = {}
        psources: dict = {}
        layers: list[tuple[etree._Element | None, etree._Element | None, bool]] = []
        if self.defaults is not None:
            layers.append((self.defaults.find(f"{qn('w:pPrDefault')}/{qn('w:pPr')}"),
                           self.defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}"), False))
        # Table-style context is explicit evidence. Conditional styling needs its own resolver.
        tables = paragraph.xpath("ancestor::w:tbl[1]", namespaces=NS)
        if tables:
            ts = tables[0].find(f"{qn('w:tblPr')}/{qn('w:tblStyle')}")
            if ts is not None:
                for style in self.chain(ts.get(qn("w:val"), "")):
                    layers.append((style.find(qn("w:pPr")), style.find(qn("w:rPr")), True))
                    if style.find(qn("w:tblStylePr")) is not None:
                        self.issues.add("conditional_table_style_needs_review")
        for style in self.chain(style_id):
            layers.append((style.find(qn("w:pPr")), style.find(qn("w:rPr")), True))
        layers.append((ppr, None, False))
        for p_layer, r_layer, is_style in layers:
            layer_properties = para_props(p_layer)
            first_line_keys = ("paragraph.indent.first_line", "paragraph.indent.first_line_chars")
            hanging_keys = ("paragraph.indent.hanging", "paragraph.indent.hanging_chars")
            # These are alternative representations of the first-line offset.
            # A more-specific layer choosing one replaces the inherited other;
            # preserve same-layer conflicts so the checker can reject them.
            if any(key in layer_properties for key in hanging_keys) and not any(key in layer_properties for key in first_line_keys):
                for key in first_line_keys:
                    praw.pop(key, None)
                    psources.pop(key, None)
            elif any(key in layer_properties for key in first_line_keys) and not any(key in layer_properties for key in hanging_keys):
                for key in hanging_keys:
                    praw.pop(key, None)
                    psources.pop(key, None)
            for key, value in layer_properties.items():
                praw[key] = value
                prefix = "word/styles.xml:" if p_layer.getroottree().getroot().tag == qn("w:styles") else ""
                psources[key] = [f"{prefix}{location(p_layer)}"]
            # para_props historically treats present false boolean nodes as true.
            if p_layer is not None:
                for name, key in (("keepNext", "paragraph.keep_next"), ("pageBreakBefore", "paragraph.page_break_before"),
                                  ("keepLines", "paragraph.keep_lines"), ("widowControl", "paragraph.widow_control")):
                    child = p_layer.find(qn(f"w:{name}"))
                    if child is not None:
                        praw[key] = on(child)
                        psources[key] = [location(child)]
            self._run(rvalues, rsources, r_layer, style=is_style)
        props, sources = self._normalize_paragraph(praw, psources)
        runs = []
        for run in paragraph.findall(f".//{qn('w:r')}"):
            # Nested text-box paragraphs are visited independently, not as outer paragraph text.
            if run.xpath("ancestor::w:p[1]", namespaces=NS)[0] is not paragraph:
                continue
            if include_whitespace:
                text = "".join(
                    (node.text or "") if node.tag == qn("w:t") else "\t" if node.tag == qn("w:tab") else "\n"
                    for node in run.iter() if node.tag in {qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")}
                )
            else:
                text = "".join(run.itertext(qn("w:t")))
            if not text or (not include_whitespace and not text.strip()):
                continue
            values = dict(rvalues)
            origins = {k: list(v) for k, v in rsources.items()}
            rpr = run.find(qn("w:rPr"))
            rstyle = rpr.find(qn("w:rStyle")) if rpr is not None else None
            if rstyle is not None:
                for style in self.chain(rstyle.get(qn("w:val"), "")):
                    self._run(values, origins, style.find(qn("w:rPr")), style=True)
            self._run(values, origins, rpr)
            runs.append({"path": location(run), "text": text if include_whitespace else text[:160], "character_count": len(text),
                         "script_character_counts": {"eastAsia": sum('\u3400' <= c <= '\u9fff' for c in text),
                                                     "latin": sum(c.isascii() and c.isalnum() for c in text)},
                         "properties": values, "property_sources": origins})
        if not runs:
            # Paragraph-mark formatting is evidence for an empty field, not a fabricated run.
            mark = ppr.find(qn("w:rPr")) if ppr is not None else None
            self._run(rvalues, rsources, mark)
            props.update(rvalues)
            sources.update(rsources)
        return props, sources, runs, style_id

    @staticmethod
    def _normalize_paragraph(raw: dict, origins: dict) -> tuple[dict, dict]:
        values, sources = {}, {}

        def put(key: str, value: Any, *original: str) -> None:
            if value is not None:
                values[key] = value
                sources[key] = list(dict.fromkeys(s for p in original for s in origins.get(p, [])))

        for key in ("paragraph.align", "paragraph.keep_next", "paragraph.page_break_before",
                    "paragraph.keep_lines", "paragraph.widow_control"):
            if key in raw:
                put(key, raw[key], key)
        for name in ("before", "after"):
            line_key, length_key = f"paragraph.spacing.{name}_lines", f"paragraph.spacing.{name}"
            if line_key in raw:
                put(length_key, measure(raw[line_key], 100, "line"), line_key)
            elif length_key in raw:
                put(length_key, measure(raw[length_key], 20, "pt"), length_key)
        line_key = "paragraph.spacing.line"
        if line_key in raw:
            rule = raw.get("paragraph.spacing.line_rule", "auto")
            put(line_key, measure(raw[line_key], 240 if rule == "auto" else 20,
                                  "multiple" if rule == "auto" else "pt", rule),
                line_key, "paragraph.spacing.line_rule")
        for name in ("left", "right", "first_line", "hanging"):
            key = f"paragraph.indent.{name}"
            chars = f"{key}_chars"
            if chars in raw:
                put(key, measure(raw[chars], 100, "character"), chars)
            elif key in raw:
                put(key, measure(raw[key], 20, "pt"), key)
        return values, sources
