"""Public FAQ loading and Telegram rendering helpers.

The FAQ is deliberately stored as a public document so product answers can be
reviewed without exposing internal implementation material to subscribers or
the AI provider.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

_ALLOWED_TAGS = ("b", "/b", "i", "/i", "code", "/code")
_FAQ_NUMBER_EMOJIS = (
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
    "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
)


def _resolve_faq_path(path: str) -> Path:
    """Resolve configured relative FAQ paths independently of process cwd."""
    configured = Path(str(path).strip())
    if configured.is_file():
        return configured
    if not configured.is_absolute():
        project_relative = Path(__file__).resolve().parents[2] / configured
        if project_relative.is_file():
            return project_relative
    return configured


def load_public_faq(path: str, *, max_chars: int = 12000) -> str:
    """Read the configured public FAQ, returning a safe UNKNOWN marker on failure."""
    try:
        content = _resolve_faq_path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "FAQ status: UNKNOWN. The public FAQ is temporarily unavailable."
    content = content.strip()
    if not content:
        return "FAQ status: UNKNOWN. The public FAQ is empty."
    return content[:max(1000, int(max_chars))]


def parse_faq_entries(markdown: str) -> list[tuple[str, list[str]]]:
    """Extract public question headings and their answer lines."""
    entries: list[tuple[str, list[str]]] = []
    question: str | None = None
    answer: list[str] = []

    def flush() -> None:
        nonlocal question, answer
        if question:
            entries.append((question, answer))
        question = None
        answer = []

    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            flush()
            question = line[3:].strip()
        elif line.startswith("# "):
            # Public metadata and document titles are intentionally not shown.
            continue
        elif question is not None:
            answer.append(line)
    flush()
    return entries


def _render_faq_line(raw_line: str) -> str:
    """Render one customer-facing answer line for Telegram HTML."""
    line = raw_line.strip()
    if not line:
        return ""
    if line.startswith("- "):
        line = f"• {line[2:].strip()}"
    line = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", line)
    escaped = html.escape(line, quote=False)
    for tag in _ALLOWED_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def render_faq_page(
    markdown: str,
    *,
    page: int = 0,
    page_size: int = 10,
    max_chars: int = 3600,
) -> tuple[str, bool, bool]:
    """Render one numbered FAQ page and return text, has_previous, has_next."""
    entries = parse_faq_entries(markdown)
    safe_page_size = max(1, int(page_size))
    total_pages = max(1, (len(entries) + safe_page_size - 1) // safe_page_size)
    safe_page = min(max(0, int(page)), total_pages - 1)
    start = safe_page * safe_page_size
    selected = entries[start:start + safe_page_size]

    rendered: list[str] = []
    for index, (question, answer_lines) in enumerate(selected):
        number = _FAQ_NUMBER_EMOJIS[index] if index < len(_FAQ_NUMBER_EMOJIS) else f"{index + 1}."
        rendered.append(f"{number} <b>{html.escape(question.upper(), quote=False)}</b>")
        answer = "\n".join(
            rendered_line
            for rendered_line in (_render_faq_line(line) for line in answer_lines)
            if rendered_line
        )
        if answer:
            rendered.append(answer)

    if not rendered:
        rendered.append(_render_faq_line(str(markdown or "FAQ is temporarily unavailable.")))
    return (
        "\n\n".join(rendered).strip()[:max(1000, int(max_chars))],
        safe_page > 0,
        safe_page < total_pages - 1,
    )


def render_faq_for_telegram(markdown: str, *, max_chars: int = 3600) -> str:
    """Render the first FAQ page for backwards-compatible callers."""
    rendered, _, _ = render_faq_page(markdown, page=0, page_size=10, max_chars=max_chars)
    return rendered
