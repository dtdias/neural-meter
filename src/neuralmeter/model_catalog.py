"""Static public model-ID allowlist; labels never echo transcript strings.

IDs are taken from provider documentation, not inferred from pricing, aliases, or
private Antigravity UI labels. Public API ID does not assert CLI/account access.
"""

from __future__ import annotations

import re

MODEL_ID_SOURCES = {
    "google": "https://ai.google.dev/gemini-api/docs/models",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/models/overview",
    "openai": "https://developers.openai.com/api/docs/models",
    "cross_reference": (
        "https://github.com/splunk/token-meter/blob/f8a767/token_meter/models/catalog.py"
    ),
}

# Provider docs checked 2026-11-16. Gemini 2.x IDs retained only to label
# historical transcript rows; some are retired. Specialized audio/image model
# IDs and unverified Antigravity-specific suffixes are intentionally excluded.
_PUBLIC_IDS = (
    # Google Gemini text/reasoning model IDs.
    "gemini-4-carbon",
    "gemini-4-argon",
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.6",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    # Anthropic Claude IDs, including retired/historical lines listed by docs.
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4",
    "claude-opus-3",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-sonnet-3-7",
    "claude-sonnet-3-5",
    "claude-haiku-5-5",
    "claude-haiku-4-5",
    "claude-haiku-3-5",
    "claude-haiku-3",
    # OpenAI GPT text/reasoning IDs from official model catalog.
    "gpt-6-astra",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-luna",
    "gpt-6-cyber",
    "gpt-5.6",
    "gpt-5.6-cyber",
)

_PUBLIC_MODEL_BY_CASEFOLD = {model.casefold(): model for model in _PUBLIC_IDS}
_PUBLIC_MODEL_BY_LABEL = {}
for _public_model in _PUBLIC_IDS:
    _label_key = re.sub(r"[^a-z0-9]+", "-", _public_model.casefold()).strip("-")
    if _label_key in _PUBLIC_MODEL_BY_LABEL:
        _PUBLIC_MODEL_BY_LABEL[_label_key] = None
    else:
        _PUBLIC_MODEL_BY_LABEL[_label_key] = _public_model

_KNOWN_SELECTION_QUALIFIERS = frozenset({"high", "medium", "low", "thinking"})


def canonical_public_model_id(value: object) -> str | None:
    """Return exact public ID after case-fold only; reject whitespace/suffixes."""
    if not isinstance(value, str) or not value or len(value) > 80:
        return None
    return _PUBLIC_MODEL_BY_CASEFOLD.get(value.casefold())


def canonical_public_model_label(value: object) -> str | None:
    """Map bounded AGY display label to public ID; never return generated slug."""
    direct = canonical_public_model_id(value)
    if direct is not None:
        return direct
    if not isinstance(value, str) or not value or len(value) > 160:
        return None
    label = value.strip()
    qualifier = re.search(r"\s*\((high|medium|low|thinking)\)\s*$", label, re.IGNORECASE)
    if qualifier and qualifier.group(1).casefold() in _KNOWN_SELECTION_QUALIFIERS:
        label = label[: qualifier.start()].rstrip()
    key = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    return _PUBLIC_MODEL_BY_LABEL.get(key)


def display_model_id(value: object) -> str:
    """Never place unverified transcript/model text in a visible label."""
    return canonical_public_model_id(value) or "Modelo não identificado"
