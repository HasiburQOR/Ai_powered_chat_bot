"""Safe minimal formatting for chat bubbles.

The bot's prompt now asks the model to mark the key fact with **bold** and to
lay out lists as → / - pointer lines. Auto-escaping would show the asterisks
literally, so bot text goes through this filter instead: HTML-escape FIRST
(whatever the model — or a visitor echoing markup — produced can never inject
tags), then convert the single supported construct (**bold**) into <strong>.
Unpaired asterisks are left alone as harmless punctuation.
"""
import re
from html import escape

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Paired **...** with non-space edges, e.g. **Baku, 12-16 Dec**.
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)


@register.filter(name="chat_format")
def chat_format(value):
    """Escape HTML, then render **bold** as <strong> (returns safe HTML)."""
    escaped = escape(str(value or ""))
    return mark_safe(_BOLD_RE.sub(r"<strong>\1</strong>", escaped))
