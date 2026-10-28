"""AG-UI 1.0 SSE stream of the transplant loop.

Stdlib-only event builders plus a synchronous generator that replays the
real ``agent.py`` steps in fixed order so judges see tool calls as
protocol events. Every emitted line is ``data: <json>\\n\\n`` and every
payload is scrubbed of key material before serialization.
"""

from __future__ import annotations

import json
import os
import uuid
from statistics import median

try:
    from backend.app import agent as _agent
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import agent as _agent


def new_run_ids() -> tuple[str, str]:
    """Return a fresh ``(threadId, runId)`` pair."""
    return uuid.uuid4().hex, uuid.uuid4().hex


def _scrub_string(value: str, secrets: list[str]) -> str:
    out = value
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, "[redacted]")
    if "sk-" in out:
        out = out.replace("sk-", "[redacted]")
    return out


def scrub(value):
    """Deep-copy ``value`` redacting live keys and any ``sk-`` substring."""
    qloo_key = os.environ.get("QLOO_API_KEY") or ""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or ""
    secrets = [s for s in (qloo_key, openrouter_key) if s]

    def _walk(node):
        if isinstance(node, str):
            return _scrub_string(node, secrets)
        if isinstance(node, dict):
            return {
                (_walk(key) if isinstance(key, str) else key): _walk(val)
                for key, val in node.items()
            }
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, tuple):
            return tuple(_walk(item) for item in node)
        return node

    return _walk(value)


def sse_encode(event: dict) -> str:
    """Encode one AG-UI event as an SSE ``data:`` frame, scrubbed."""
    cleaned = scrub(event)
    text = json.dumps(cleaned, default=str)
    # Belt-and-braces: scrub the serialized line itself.
    qloo_key = os.environ.get("QLOO_API_KEY") or ""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or ""
    for secret in (qloo_key, openrouter_key):
        if secret and secret in text:
            text = text.replace(secret, "[redacted]")
    if "sk-" in text:
        text = text.replace("sk-", "[redacted]")
    return "data: " + text + "\n\n"


def run_started(thread_id: str, run_id: str) -> dict:
    return {"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id}


def tool_start(tool_call_id: str, tool_call_name: str, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TOOL_CALL_START",
        "threadId": thread_id,
        "runId": run_id,
        "toolCallId": tool_call_id,
        "toolCallName": tool_call_name,
    }


def tool_args(tool_call_id: str, args: object, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TOOL_CALL_ARGS",
        "threadId": thread_id,
        "runId": run_id,
        "toolCallId": tool_call_id,
        "args": args if isinstance(args, dict) else {"args": args},
    }


def tool_end(tool_call_id: str, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TOOL_CALL_END",
        "threadId": thread_id,
        "runId": run_id,
        "toolCallId": tool_call_id,
    }


def tool_result(tool_call_id: str, result: object, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TOOL_CALL_RESULT",
        "threadId": thread_id,
        "runId": run_id,
        "toolCallId": tool_call_id,
        "result": result,
        "content": result,
    }


def state_delta(delta: dict, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "STATE_DELTA",
        "threadId": thread_id,
        "runId": run_id,
        "delta": delta,
    }


def state_snapshot(snapshot: dict, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "STATE_SNAPSHOT",
        "threadId": thread_id,
        "runId": run_id,
        "snapshot": snapshot,
    }


def text_start(message_id: str, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TEXT_MESSAGE_START",
        "threadId": thread_id,
        "runId": run_id,
        "messageId": message_id,
    }


def text_content(message_id: str, content: str, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TEXT_MESSAGE_CONTENT",
        "threadId": thread_id,
        "runId": run_id,
        "messageId": message_id,
        "delta": content,
        "content": content,
        "message": content,
    }


def text_end(message_id: str, thread_id: str = "", run_id: str = "") -> dict:
    return {
        "type": "TEXT_MESSAGE_END",
        "threadId": thread_id,
        "runId": run_id,
        "messageId": message_id,
    }


def run_finished(thread_id: str, run_id: str, result: dict | None = None) -> dict:
    event: dict = {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id}
    if result is not None:
        event["result"] = result
    return event


def run_error(message: str, thread_id: str = "", run_id: str = "", code: str = "ERROR") -> dict:
    return {
        "type": "RUN_ERROR",
        "threadId": thread_id,
        "runId": run_id,
        "message": message,
        "code": code,
    }


def _new_tool_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


def iter_transplant_events(seed_ids, city, refinements=None):
    """Stream the transplant loop as AG-UI events. Never raises.

    Wraps the real ``agent.py`` callables step by step: free-text seeds
    resolve via ``_search_fn`` (``qloo_search``), places via
    ``_fallback_entities`` (``qloo_insights_place``), brands via
    ``_brand_names`` (``qloo_insights_brand``), copy via ``_summarize_fn``
    (``summarize_panes``). Any exception after ``RUN_STARTED`` streams as
    a single ``RUN_ERROR``.
    """
    thread_id, run_id = new_run_ids()
    message_id = uuid.uuid4().hex
    city_display = city.strip() if isinstance(city, str) and city.strip() else "London"
    refs = [r.strip() for r in (refinements or []) if isinstance(r, str) and r.strip()]
    seeds = list(seed_ids or [])
    try:
        yield sse_encode(run_started(thread_id, run_id))

        # Resolve seeds, emitting qloo_search only for free-text seeds.
        signals: list[str] = []
        confirmations: list[dict] = []
        labels: list[str] = []
        for raw in seeds:
            text = raw.strip() if isinstance(raw, str) else str(raw).strip()
            if not text:
                continue
            if _agent._is_urn(text):
                signals.append(text)
                labels.append(_agent._short_label(text))
                continue
            labels.append(text)
            call_id = _new_tool_id()
            yield sse_encode(tool_start(call_id, "qloo_search", thread_id, run_id))
            yield sse_encode(tool_args(call_id, {"query": text}, thread_id, run_id))
            try:
                results = _agent._search_fn()(text)
            except Exception:
                results = []
            yield sse_encode(tool_end(call_id, thread_id, run_id))
            yield sse_encode(tool_result(call_id, results if isinstance(results, (list, dict)) else [], thread_id, run_id))
            if isinstance(results, list):
                items = results
            elif isinstance(results, dict) and isinstance(results.get("results"), list):
                items = results["results"]
            else:
                items = []
            candidates: list[dict] = []
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                entity_id = item.get("entity_id") or item.get("id")
                if not name or not entity_id:
                    continue
                if not isinstance(entity_id, str) or not entity_id.startswith("urn:entity:"):
                    continue
                raw_conf = item.get("confidence", item.get("match_confidence", 0.9))
                try:
                    confidence = float(raw_conf)
                except (TypeError, ValueError):
                    confidence = 0.9
                candidates.append(
                    {"name": str(name), "entity_id": str(entity_id), "confidence": confidence}
                )
            confirmations.append({"text": text, "candidates": candidates})
            if candidates:
                signals.append(candidates[0]["entity_id"])

        # Place insights around the real fallback ladder.
        place_call_id = _new_tool_id()
        yield sse_encode(tool_start(place_call_id, "qloo_insights_place", thread_id, run_id))
        yield sse_encode(
            tool_args(
                place_call_id,
                {"location": city_display, "entity_ids": signals or None, "take": 8},
                thread_id,
                run_id,
            )
        )
        entities, broadened = _agent._fallback_entities(city_display, signals, refinements=refs)
        if not isinstance(entities, list):
            entities = []
        yield sse_encode(tool_end(place_call_id, thread_id, run_id))
        yield sse_encode(
            tool_result(place_call_id, {"entities": entities[:8]}, thread_id, run_id)
        )

        # Card/pin math identical to agent.run_transplant.
        picked = entities[:5]
        scores: list[float] = []
        for entity in picked:
            try:
                scores.append(float(entity.get("explainability", 1.0) or 1.0))
            except (TypeError, ValueError):
                scores.append(1.0)
        try:
            mid = float(median(scores)) or 1.0
        except (ValueError, TypeError):
            mid = 1.0
        center = list(_agent._FALLBACK_CENTERS.get(city_display.strip().lower(), [51.5074, -0.1278]))
        seed_hint = labels[0] if labels else city_display
        cards: list[dict] = []
        for index, entity in enumerate(picked):
            name = str(entity.get("name", ""))
            tags = entity.get("tags") if isinstance(entity.get("tags"), list) else []
            tag_hint = str(tags[0]).replace("-", " ") if tags else (refs[0] if refs else "your crowd")
            latlng = [round(center[0] + 0.01 * index, 4), round(center[1] + 0.012 * index, 4)]
            cards.append(
                {
                    "name": name,
                    "address": str(entity.get("address", "")),
                    "latlng": latlng,
                    "why_you": f"Because {seed_hint} nights and {tag_hint} both live here",
                    "affinity_multiplier": round(scores[index] / mid, 1),
                    "taste_path": [seed_hint, tag_hint, name],
                    "entity_id": str(entity.get("entity_id", "")),
                }
            )
        map_pins = [{"latlng": card["latlng"], "card_index": i} for i, card in enumerate(cards)]
        yield sse_encode(
            state_delta(
                {"cards": cards, "pins": map_pins, "map_pins": map_pins, "map_center": center},
                thread_id,
                run_id,
            )
        )

        # Brand kicker around the real brand helper.
        brand_call_id = _new_tool_id()
        yield sse_encode(tool_start(brand_call_id, "qloo_insights_brand", thread_id, run_id))
        yield sse_encode(
            tool_args(
                brand_call_id,
                {"location": city_display, "entity_ids": signals or None, "take": 5},
                thread_id,
                run_id,
            )
        )
        try:
            brands = _agent._brand_names(city_display, signals or None, take=5, refinements=refs)
        except Exception:
            brands = []
        if not isinstance(brands, list):
            brands = []
        yield sse_encode(tool_end(brand_call_id, thread_id, run_id))
        yield sse_encode(tool_result(brand_call_id, {"brands": brands}, thread_id, run_id))
        yield sse_encode(state_delta({"brands": brands}, thread_id, run_id))

        # Summaries around the real summarizer.
        summarize_call_id = _new_tool_id()
        yield sse_encode(tool_start(summarize_call_id, "summarize_panes", thread_id, run_id))
        yield sse_encode(
            tool_args(
                summarize_call_id,
                {"words": labels or [city_display], "cards": [c.get("name") for c in picked if isinstance(c, dict)]},
                thread_id,
                run_id,
            )
        )
        tourist_summary = (
            f"Tourist take on {city_display}: the usual top-10 list, no reasons attached."
        )
        transplant_summary = f"Grounded transplant of {len(seeds)} tastes to {city_display}."
        try:
            panes = _agent._summarize_fn()(labels or [city_display], {"cards": picked})
            if isinstance(panes, dict):
                tourist = panes.get("tourist") if isinstance(panes.get("tourist"), dict) else {}
                transplant = panes.get("transplant") if isinstance(panes.get("transplant"), dict) else {}
                if isinstance(tourist.get("summary"), str) and tourist["summary"].strip():
                    tourist_summary = tourist["summary"]
                if isinstance(transplant.get("summary"), str) and transplant["summary"].strip():
                    transplant_summary = transplant["summary"]
                overrides = transplant.get("cards") if isinstance(transplant, dict) else []
                if isinstance(overrides, list):
                    by_name = {
                        str(c.get("name")): str(c.get("why_you"))
                        for c in overrides
                        if isinstance(c, dict) and c.get("name") and c.get("why_you")
                    }
                    for card in cards:
                        if card["name"] in by_name:
                            card["why_you"] = by_name[card["name"]]
        except Exception:
            pass
        yield sse_encode(tool_end(summarize_call_id, thread_id, run_id))
        yield sse_encode(
            tool_result(
                summarize_call_id,
                {"tourist": {"summary": tourist_summary}, "transplant": {"summary": transplant_summary}},
                thread_id,
                run_id,
            )
        )
        yield sse_encode(
            state_delta(
                {
                    "cards": cards,
                    "tourist": {"summary": tourist_summary},
                    "transplant": {"summary": transplant_summary},
                    "tourist_summary": tourist_summary,
                    "transplant_summary": transplant_summary,
                },
                thread_id,
                run_id,
            )
        )

        # Narration as a text message, then snapshot + finish.
        yield sse_encode(text_start(message_id, thread_id, run_id))
        yield sse_encode(text_content(message_id, transplant_summary, thread_id, run_id))
        yield sse_encode(text_end(message_id, thread_id, run_id))
        yield sse_encode(
            state_snapshot(
                {
                    "cards": cards,
                    "pins": map_pins,
                    "map_pins": map_pins,
                    "map_center": center,
                    "brands": brands,
                    "tourist_summary": tourist_summary,
                    "transplant_summary": transplant_summary,
                    "city": city_display,
                    "broadened": broadened,
                },
                thread_id,
                run_id,
            )
        )
        yield sse_encode(run_finished(thread_id, run_id))
    except Exception as exc:
        try:
            message = str(exc) or "transplant failed"
        except Exception:
            message = "transplant failed"
        yield sse_encode(run_error(message, thread_id, run_id))
