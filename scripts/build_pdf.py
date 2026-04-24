"""Build a preview PDF from the markdown chapter drafts.

Usage:
    python -m scripts.build_pdf                   # Ch.3 + Ch.4 preview
    python -m scripts.build_pdf ch01 ch04         # custom chapters

Output: reports/bridge_report_preview.pdf

Design decisions:
- Embeds a real TTF/OTF CJK font so the PDF renders in any viewer
  (macOS Preview, Chrome, Acrobat). Resolution order:
    1. fonts/NotoSansCJKtc-Regular.otf (if user pre-placed it)
    2. Extract from macOS system TTC (PingFang / Heiti / Songti) via fontTools
    3. Download Noto Sans TC from public CDN (jsdelivr / github)
- Handles a subset of markdown: H1–H4, paragraphs, bold, code spans,
  fenced code blocks, list items, blockquotes, tables (as preformatted)
- NOT a full markdown-to-PDF converter — just enough to produce a
  shareable preview for the interview
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import os
import urllib.request

from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Preformatted,
)

# --- CJK font resolution ---
# Tries in order:
#   1. fonts/NotoSansCJKtc-Regular.otf in repo (if user already downloaded)
#   2. macOS system fonts (PingFang / Heiti via fontTools TTC extraction)
#   3. Download Noto Sans TC from public CDN (with multiple fallback URLs)
#
# The result is a real embedded TTF/OTF, so the PDF renders in any viewer.

FONTS_DIR = REPO_ROOT / "fonts"
# reportlab 的 TTFont 只吃 TrueType (glyf) 格式，**不吃 CFF (PostScript) outline 的 OpenType**。
# macOS 系統字型（PingFang/Songti/Hiragino）多為 CFF 格式，無法用。
# 直接下載 Google Fonts 的 Noto Sans TC **static TTF**（真 TrueType）最可靠。
REPO_FONT_CANDIDATES = [
    FONTS_DIR / "NotoSansTC-Regular.ttf",
    FONTS_DIR / "NotoSansCJKtc-Regular.ttf",
]
DOWNLOAD_URLS = [
    # @fontsource 的 npm 包透過 jsdelivr / unpkg 直接提供 TTF，不走 Git LFS
    # Google Fonts repo (raw.githubusercontent.com) 存的是 LFS pointer → 會拿到錯誤 HTML
    "https://cdn.jsdelivr.net/npm/@fontsource/noto-sans-tc/files/noto-sans-tc-chinese-traditional-400-normal.ttf",
    "https://unpkg.com/@fontsource/noto-sans-tc/files/noto-sans-tc-chinese-traditional-400-normal.ttf",
    "https://cdn.jsdelivr.net/npm/@fontsource/noto-sans-tc@5.0.5/files/noto-sans-tc-chinese-traditional-400-normal.ttf",
]


def _is_valid_truetype(path: Path) -> bool:
    """Check magic bytes to confirm the file is TrueType (glyf) — not HTML / CFF / LFS pointer."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        # TrueType: 0x00010000 or "true"
        # OpenType-CFF: "OTTO"  (reportlab can't render these)
        # Anything else = corrupt / wrong format / HTML error page
        return magic in (b"\x00\x01\x00\x00", b"true")
    except OSError:
        return False

CJK_FONT = "bridgeCJK"


def _download(url: str, dest: Path) -> bool:
    try:
        print(f"  trying {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "bridge-analyst/0.1"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            f.write(r.read())
        return True
    except Exception as e:
        print(f"  failed: {e}")
        return False


def _resolve_cjk_font() -> Path:
    """Return path to a TrueType (glyf) CJK font. Raises if none work.

    Strategy: only TrueType (not CFF) fonts work with reportlab.TTFont.
    - macOS system fonts (PingFang/Songti) are CFF → skipped.
    - Download Noto Sans TC static TTF (真 TrueType) from Google Fonts.
    """
    # Clean up previously-extracted/bad fonts if present (from older script versions)
    for stale_name in ("MacExtracted-Regular.ttf",):
        stale = FONTS_DIR / stale_name
        if stale.exists():
            try:
                stale.unlink()
                print(f"Removed stale font file: {stale}")
            except OSError:
                pass

    # 1. Already in repo AND valid TrueType? Otherwise delete and re-download.
    for p in REPO_FONT_CANDIDATES:
        if p.exists():
            if _is_valid_truetype(p):
                print(f"Using repo font: {p}")
                return p
            else:
                print(f"Repo font {p} is not valid TrueType (HTML/CFF/LFS pointer)."
                      f" Deleting and re-downloading.")
                try:
                    p.unlink()
                except OSError:
                    pass

    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Download Noto Sans TC TTF from jsdelivr/unpkg @fontsource
    target = FONTS_DIR / "NotoSansTC-Regular.ttf"
    print(f"Downloading Noto Sans TC TTF to {target} ...")
    for url in DOWNLOAD_URLS:
        if _download(url, target):
            if _is_valid_truetype(target):
                print(f"  → valid TTF from {url}")
                return target
            else:
                with open(target, "rb") as f:
                    magic = f.read(4)
                print(f"  downloaded but not valid TrueType (magic={magic!r}). "
                      f"Trying next URL.")
                target.unlink()

    raise RuntimeError(
        "\n無法自動取得有效的 CJK TrueType 字型。請手動下載：\n"
        "  1. 開瀏覽器到 https://fonts.google.com/noto/specimen/Noto+Sans+TC\n"
        "  2. 點右上「Get font」→「Download all」\n"
        f"  3. 解壓後把 static/NotoSansTC-Regular.ttf 複製到 {FONTS_DIR}/\n"
        "\n或用 curl：\n"
        f"  curl -L -o {FONTS_DIR}/NotoSansTC-Regular.ttf \\\n"
        f"    {DOWNLOAD_URLS[0]}\n"
    )


_font_path = _resolve_cjk_font()
pdfmetrics.registerFont(TTFont(CJK_FONT, str(_font_path)))


def _styles() -> dict:
    """Return a dict of named ParagraphStyle objects tuned for CJK."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontName=CJK_FONT, fontSize=22, leading=28,
            spaceAfter=18, alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"],
            fontName=CJK_FONT, fontSize=18, leading=24,
            spaceBefore=18, spaceAfter=10,
            textColor="#2c4a6b",
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontName=CJK_FONT, fontSize=15, leading=20,
            spaceBefore=14, spaceAfter=8,
            textColor="#2c4a6b",
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"],
            fontName=CJK_FONT, fontSize=13, leading=17,
            spaceBefore=10, spaceAfter=6,
            textColor="#444",
        ),
        "h4": ParagraphStyle(
            "h4", parent=base["Heading4"],
            fontName=CJK_FONT, fontSize=11, leading=15,
            spaceBefore=8, spaceAfter=4,
            textColor="#555",
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontName=CJK_FONT, fontSize=10, leading=16,
            spaceAfter=6, alignment=TA_LEFT,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base["BodyText"],
            fontName=CJK_FONT, fontSize=10, leading=16,
            leftIndent=16, rightIndent=8,
            textColor="#555",
            spaceAfter=6,
            borderPadding=6,
        ),
        "list": ParagraphStyle(
            "list", parent=base["BodyText"],
            fontName=CJK_FONT, fontSize=10, leading=15,
            leftIndent=18, spaceAfter=2,
        ),
        # IMPORTANT: use CJK_FONT (not Courier) so code blocks / tables that
        # contain CJK characters render correctly. CJK font has ASCII glyphs too,
        # so pure-ASCII code still looks ok (just not monospaced).
        "code": ParagraphStyle(
            "code", parent=base["Code"],
            fontName=CJK_FONT, fontSize=8, leading=11,
            backColor="#f5f5f5",
            borderPadding=4, leftIndent=8,
        ),
    }


# --- Inline markup converter (markdown → reportlab minihtml) ---

def _inline(text: str) -> str:
    """Convert inline markdown to reportlab's HTML-like markup."""
    # Escape <, >, & first (but keep our generated tags after)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold **text**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    # Italic *text* (not matching ** already converted)
    text = re.sub(r"(?<![*\w])\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    # Inline code `text` — use CJK font (still readable for ASCII) so that
    # `sentiment_extractor@v2` and `淡江大橋` both render. Background color
    # gives visual distinction from body text.
    text = re.sub(
        r"`([^`]+)`",
        lambda m: f'<font face="{CJK_FONT}" backcolor="#f0f0f0">{m.group(1)}</font>',
        text,
    )
    # Links [text](url) — just keep the text, append url in parens smaller
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'\1 <font size="8" color="#888">(\2)</font>',
        text,
    )
    return text


# --- Block-level parser ---

def _parse_markdown(md: str, styles: dict) -> list:
    """Parse markdown into a list of reportlab flowables.

    Handles (line-by-line, no nesting):
    - # / ## / ### / #### headings
    - fenced ```code blocks```
    - > blockquotes (single line)
    - - bullet list items
    - | table | rows | — rendered as preformatted text
    - blank line = paragraph break
    - Everything else = paragraph (inline markup applied)
    """
    flowables: list = []
    lines = md.splitlines()
    i = 0
    buf: list[str] = []

    def flush_paragraph():
        nonlocal buf
        if buf:
            text = " ".join(s.strip() for s in buf if s.strip())
            if text:
                flowables.append(Paragraph(_inline(text), styles["body"]))
            buf = []

    while i < len(lines):
        line = lines[i]

        # Fenced code block
        if line.startswith("```"):
            flush_paragraph()
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            code_text = "\n".join(code_lines)
            flowables.append(Preformatted(code_text, styles["code"]))
            flowables.append(Spacer(1, 4))
            continue

        # Horizontal rule
        if re.match(r"^\s*---+\s*$", line):
            flush_paragraph()
            flowables.append(Spacer(1, 8))
            i += 1
            continue

        # Headings
        h_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if h_match:
            flush_paragraph()
            level = len(h_match.group(1))
            heading_text = _inline(h_match.group(2).strip())
            style_key = f"h{level}"
            flowables.append(Paragraph(heading_text, styles[style_key]))
            i += 1
            continue

        # Blockquote (use ASCII-compatible pipe marker; "▎" is not in all CJK fonts)
        if line.startswith(">"):
            flush_paragraph()
            quote_text = _inline(line.lstrip("> ").strip())
            flowables.append(Paragraph(f"| {quote_text}", styles["quote"]))
            i += 1
            continue

        # Bulleted list item (use "-" instead of "•" — Unicode bullet U+2022
        # isn't in the extracted PingFang subfont we use on macOS, so it shows
        # as garbage. "-" works in every font.)
        bullet_match = re.match(r"^[\s]*[-*]\s+(.+)$", line)
        if bullet_match:
            flush_paragraph()
            item_text = _inline(bullet_match.group(1).strip())
            flowables.append(Paragraph(f"- {item_text}", styles["list"]))
            i += 1
            continue

        # Numbered list item
        num_match = re.match(r"^[\s]*(\d+)\.\s+(.+)$", line)
        if num_match:
            flush_paragraph()
            item_text = _inline(num_match.group(2).strip())
            flowables.append(Paragraph(
                f"{num_match.group(1)}. {item_text}", styles["list"]
            ))
            i += 1
            continue

        # Table row — just preformat (no proper table rendering)
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            flush_paragraph()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            flowables.append(Preformatted("\n".join(table_lines), styles["code"]))
            flowables.append(Spacer(1, 4))
            continue

        # Blank line → paragraph boundary
        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        # Default: accumulate into paragraph
        buf.append(line)
        i += 1

    flush_paragraph()
    return flowables


# --- Main ---

CHAPTER_FILES = {
    "ch01": ("Ch.1 議題地景", "reports/ch01_draft.md"),
    "ch02": ("Ch.2 輿情結構", "reports/ch02_draft.md"),
    "ch03": ("Ch.3 AI 設計思路", "reports/ch03_draft.md"),
    "ch04": ("Ch.4 時代力量的切入機會", "reports/ch04_draft.md"),
    "ch05": ("Ch.5 執行摘要與下一步", "reports/ch05_draft.md"),
}

DEFAULT_CHAPTERS = ["ch03", "ch04"]


def main() -> int:
    chapters = sys.argv[1:] or DEFAULT_CHAPTERS
    unknown = [c for c in chapters if c not in CHAPTER_FILES]
    if unknown:
        print(f"Unknown chapters: {unknown}")
        print(f"Available: {list(CHAPTER_FILES.keys())}")
        return 2

    out_path = REPO_ROOT / "reports" / "bridge_report_preview.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    styles = _styles()

    story: list = []

    # Cover
    story.append(Paragraph("淡江大橋議題分析 — 報告預覽", styles["title"]))
    story.append(Paragraph(
        "<font size='10' color='#666'>bridge-analyst · 2026-04-24 draft · "
        f"包含章節：{' + '.join(chapters)}</font>",
        styles["body"],
    ))
    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "<font size='9' color='#888'>"
        "此為預覽版 PDF，從 markdown 草稿轉換而成。"
        "表格與程式碼區塊以等寬字型呈現，視覺樣式為報告最終版的雛形。"
        "</font>",
        styles["body"],
    ))
    story.append(PageBreak())

    # Chapters
    for i, key in enumerate(chapters):
        title, rel_path = CHAPTER_FILES[key]
        md_path = REPO_ROOT / rel_path
        if not md_path.exists():
            story.append(Paragraph(
                f"<i>[{title}] 尚未起稿（檔案不存在：{rel_path}）</i>",
                styles["body"],
            ))
            story.append(PageBreak())
            continue

        md_text = md_path.read_text(encoding="utf-8")
        flowables = _parse_markdown(md_text, styles)
        story.extend(flowables)

        if i < len(chapters) - 1:
            story.append(PageBreak())

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="bridge-analyst report preview",
        author="dorian",
    )
    doc.build(story)
    print(f"PDF written: {out_path}")
    print(f"Open with: open {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
