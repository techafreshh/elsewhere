"""Two-pane LLM copy with template fallback (Tourist vs Transplant).

One shared OpenRouter model drafts both pane summaries — the Tourist pane
from the visitor's raw words only, the Transplant pane from the same words
*plus* Qloo evidence — but the demo never 500s: a missing
``OPENROUTER_API_KEY``, a non-200 status, a timeout, or any other exception
collapses to deterministic template copy.

Public entry point: ``summarize_panes(words, evidence=None)`` returns::

    {"tourist": {"summary": str},
     "transplant": {"summary": str, "cards": [{"name": str, "why_you": str}]}}

Template copy always carries capitalised ``Tourist`` in
``tourist.summary`` and lowercase ``transplant`` in
``transplant.summary``. Stdlib-only (``urllib``, ``json``, ``os``).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = 15


def _words_display(words: object) -> str:
    """Coerce ``str | list[str]`` input to a joined display string; never raises."""
    try:
        if isinstance(words, str):
            return words.strip()
        if isinstance(words, (list, tuple)):
            parts = [str(item).strip() for item in words if str(item).strip()]
            return ", ".join(parts)
        if words is None:
            return ""
        text = str(words).strip()
        return text
    except Exception:
        return ""


def _card_names(evidence: object) -> list[str]:
    """Extract card names from ``None | dict | list`` evidence; never raises."""
    try:
        candidates: object = None
        if isinstance(evidence, dict):
            for field in ("cards", "entities", "brands"):
                value = evidence.get(field)
                if isinstance(value, list) and value:
                    candidates = value
                    break
        elif isinstance(evidence, (list, tuple)):
            candidates = list(evidence)
        if not isinstance(candidates, list) or not candidates:
            return []
        names: list[str] = []
        for item in candidates:
            if isinstance(item, dict):
                for field in ("name", "title", "label"):
                    value = item.get(field)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
                        break
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
        return names
    except Exception:
        return []


def _template_copy(words: object, evidence: object = None) -> dict:
    """Deterministic template envelope; the Never-500 floor. Never raises."""
    try:
        display = _words_display(words) or "your home tastes"
        names = _card_names(evidence)
        if not names:
            names = ["your corner spot"]
        cards = [
            {
                "name": name,
                "why_you": f"Because {display} nights belong at {name}",
            }
            for name in names
        ]
        return {
            "tourist": {
                "summary": f"Tourist take on {display}: the usual top-10 list, no reasons attached.",
            },
            "transplant": {
                "summary": f"Grounded transplant of {display}: spots where your crowd already gathers.",
                "cards": cards,
            },
        }
    except Exception:
        return {
            "tourist": {"summary": "Tourist take: the usual top-10 list."},
            "transplant": {
                "summary": "Grounded transplant: spots where your crowd gathers.",
                "cards": [{"name": "your corner spot", "why_you": "Because home travels with you"}],
            },
        }


def _tourist_messages(words_display: str) -> list[dict]:
    """Tourist prompt: raw words only, no Qloo evidence, no key material."""
    text = words_display.strip() or "your home tastes"
    return [
        {
            "role": "system",
            "content": (
                "You write the Tourist pane: what a generic travel list says "
                "about these tastes. One short paragraph, plain and stereotypical."
            ),
        },
        {
            "role": "user",
            "content": f"Write a one-paragraph Tourist summary for someone into: {text}.",
        },
    ]


def _transplant_messages(words_display: str, evidence: object = None) -> list[dict]:
    """Transplant prompt: words plus a compacted JSON excerpt of Qloo evidence."""
    text = words_display.strip() or "your home tastes"
    try:
        excerpt = json.dumps(evidence, default=str)[:2000] if evidence is not None else "no evidence yet"
    except (TypeError, ValueError):
        excerpt = "no evidence yet"
    if not excerpt:
        excerpt = "no evidence yet"
    return [
        {
            "role": "system",
            "content": (
                "You write the Transplant pane: where someone with these tastes "
                "will belong in the new city, grounded in the Qloo evidence. "
                "One short paragraph, warm and specific."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Write a one-paragraph transplant summary for someone into: {text}.\n"
                f"Qloo evidence: {excerpt}"
            ),
        },
    ]


def _why_you_messages(words_display: str, names: list[str]) -> list[dict]:
    """Why-you prompt: one JSON object mapping each card name to a line."""
    text = words_display.strip() or "your home tastes"
    listed = ", ".join(f'"{name}"' for name in names)
    return [
        {
            "role": "system",
            "content": (
                "You write per-card why-you lines: one warm, specific sentence "
                "per card explaining why someone with these tastes will belong "
                "there. Reply with ONLY a single JSON object mapping each card "
                "name to its one-sentence why-you line. No prose, no fences."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Someone into: {text}. Write a one-sentence why-you line "
                f"for exactly these cards: {listed}."
            ),
        },
    ]


def _parse_why_you(text: object, names: list[str]) -> dict[str, str]:
    """Parse a ``{name: line}`` JSON object; only known names survive."""
    try:
        if not isinstance(text, str) or not text.strip():
            return {}
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            parsed = json.loads(cleaned)
        except (TypeError, ValueError):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                parsed = json.loads(cleaned[start : end + 1])
            except (TypeError, ValueError):
                return {}
        if not isinstance(parsed, dict):
            return {}
        allowed = {name for name in names if isinstance(name, str) and name.strip()}
        out: dict[str, str] = {}
        for key, value in parsed.items():
            try:
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                name = key.strip()
                line = value.strip()
                if not name or not line or name not in allowed:
                    continue
                out[name] = line[:200]
            except Exception:
                continue
        return out
    except Exception:
        return {}


def _timeout_seconds() -> float:
    """Per-call OpenRouter timeout, read at call time via OPENROUTER_TIMEOUT."""
    try:
        return float(os.environ.get("OPENROUTER_TIMEOUT", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return float(DEFAULT_TIMEOUT)


def _chat_once(
    messages: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> str | None:
    """One OpenRouter chat-completions call; ``None`` on any failure, never raises."""
    try:
        key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return None
        resolved_model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        resolved_timeout = timeout if timeout is not None else _timeout_seconds()
        body = json.dumps(
            {
                "model": resolved_model,
                "messages": messages,
                "max_tokens": 300,
                "temperature": 0.7,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=resolved_timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            return None
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "")
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        text = content.strip() if isinstance(content, str) else ""
        return text or None
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        OSError,
    ):
        return None
    except Exception:
        return None


def summarize_panes(words: object, evidence: object = None) -> dict:
    """Summarize both panes with one model, falling back to template copy.

    Reads ``OPENROUTER_API_KEY``/``OPENROUTER_MODEL`` at call time. Returns
    the ``tourist.summary`` / ``transplant.summary`` / per-card ``why_you``
    envelope without ever raising: a missing key returns template copy
    immediately, and any LLM ``None`` or exception collapses to it too.
    """
    try:
        if not os.environ.get("OPENROUTER_API_KEY"):
            return _template_copy(words, evidence)
        display = _words_display(words) or "your home tastes"
        model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        tourist_text = _chat_once(_tourist_messages(display), model=model)
        transplant_text = _chat_once(_transplant_messages(display, evidence), model=model)
        if not tourist_text or not transplant_text:
            return _template_copy(words, evidence)
        template_cards = _template_copy(words, evidence)["transplant"]["cards"]
        try:
            why_names = [
                str(card.get("name"))
                for card in template_cards
                if isinstance(card, dict)
                and isinstance(card.get("name"), str)
                and card["name"].strip()
            ]
        except Exception:
            why_names = []
        if why_names:
            try:
                why_text = _chat_once(_why_you_messages(display, why_names), model=model)
                why_map = _parse_why_you(why_text, why_names)
            except Exception:
                why_map = {}
            if why_map:
                try:
                    merged = []
                    for card in template_cards:
                        try:
                            if (
                                isinstance(card, dict)
                                and isinstance(card.get("name"), str)
                                and card["name"] in why_map
                            ):
                                card = {**card, "why_you": why_map[card["name"]]}
                        except Exception:
                            pass
                        merged.append(card)
                    template_cards = merged
                except Exception:
                    pass
        return {
            "tourist": {"summary": tourist_text},
            "transplant": {"summary": transplant_text, "cards": template_cards},
        }
    except Exception:
        return _template_copy(words, evidence)


# Backwards-tolerant alias for the template builder.
template_copy = _template_copy
