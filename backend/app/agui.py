"""AG-UI 1.0 SSE stream of the transplant loop.

Stdlib-only event builders plus a synchronous generator that replays the
real ``agent.py`` steps in fixed order so judges see tool calls as
protocol events. Every emitted line is ``data: <json>\\n\\n`` and every
payload is scrubbed of key material before serialization.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.parse
import uuid
from statistics import median

try:
    from backend.app import agent as _agent
    from backend.app import summarizer as _summarizer
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import agent as _agent
    from app import summarizer as _summarizer

# Message id of the live generic-LLM lane so the frontend can route its
# TEXT_MESSAGE_* deltas into the tourist pane.
TOURIST_MESSAGE_ID = "tourist"

# How long the pipeline waits for the generic lane to finish before the
# final snapshot. The Qloo steps (insights + brands + summarize) already
# take 10s+, so a healthy tourist stream is done long before this; while
# waiting, the queue is drained continuously so late first tokens still
# stream instead of dying in the bounded join.
_TOURIST_JOIN_TIMEOUT = 20.0


def _tourist_lane_messages(city_display: str, words_display: str) -> list[dict]:
    """The generic-LLM question: same city, same tastes, zero Qloo data —
    deliberately the answer anyone gets from a plain chat model."""
    return [
        {
            "role": "system",
            "content": (
                "You write the Tourist pane of a moving-abroad comparison: "
                "what a generic LLM with no local data says. Give the usual "
                "obvious answer; do not invent specific hidden gems."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Someone is moving to {city_display}. At home they love: "
                f"{words_display}. Give the generic answer: 5 well-known "
                "touristy spots there, one short line each."
            ),
        },
    ]


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


def default_tourist_items(city_display: str) -> list[str]:
    """Generic tourist rows when the live lane produced prose, not lines."""
    return [
        f"Top 10 restaurants in {city_display}",
        f"Must-see sights in {city_display}",
        f"Best nightlife in {city_display}",
    ]


def iter_transplant_events(seed_ids, city, refinements=None):
    """Stream the transplant loop as AG-UI events. Never raises.

    Wraps the real ``agent.py`` callables step by step: free-text seeds
    resolve via an extract_tastes call plus per-candidate ``qloo_search``, places via
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

        # Resolve seeds, emitting visible tool calls for free-text seeds.
        # Mirrors agent.resolve_text_seed step for step: an LLM extraction
        # call, then one qloo_search per extracted candidate — every network
        # hop the JSON path makes is protocol-visible here too.
        signals: list[str] = []
        confirmations: list[dict] = []
        labels: list[str] = []
        for raw in seeds:
            text = raw.strip() if isinstance(raw, str) else str(raw).strip()
            if not text:
                continue
            if _agent._is_urn(text):
                signals.append(text)
                label = _agent._label_for_seed(text)
                if label:
                    labels.append(label)
                continue
            labels.append(text)
            extract_id = _new_tool_id()
            yield sse_encode(tool_start(extract_id, "extract_tastes", thread_id, run_id))
            yield sse_encode(tool_args(extract_id, {"text": text}, thread_id, run_id))
            try:
                candidate_queries = _agent.extract_taste_candidates(text) or []
            except Exception:
                candidate_queries = []
            if not candidate_queries:
                candidate_queries = [text]
            yield sse_encode(tool_end(extract_id, thread_id, run_id))
            yield sse_encode(
                tool_result(extract_id, {"candidates": candidate_queries}, thread_id, run_id)
            )
            merged: list[dict] = []
            seen_ids: set[str] = set()
            for query in candidate_queries[:3]:
                if not isinstance(query, str) or not query.strip():
                    continue
                search_id = _new_tool_id()
                yield sse_encode(tool_start(search_id, "qloo_search", thread_id, run_id))
                yield sse_encode(tool_args(search_id, {"query": query.strip()}, thread_id, run_id))
                try:
                    hits = _agent._search_one_candidate(query)
                except Exception:
                    hits = []
                yield sse_encode(tool_end(search_id, thread_id, run_id))
                yield sse_encode(tool_result(search_id, hits, thread_id, run_id))
                for hit in hits:
                    try:
                        entity_id = str(hit.get("entity_id", ""))
                    except Exception:
                        continue
                    if not entity_id or entity_id in seen_ids:
                        continue
                    seen_ids.add(entity_id)
                    merged.append(hit)
            confirmations.append({"text": text, "candidates": merged})
            if merged:
                try:
                    signals.append(str(merged[0]["entity_id"]))
                except Exception:
                    pass
        # Surface free-text confirmations while the heavy calls run.
        yield sse_encode(state_delta({"confirmations": confirmations}, thread_id, run_id))

        # Generic-LLM lane: stream the plain (zero-Qloo) answer alongside the
        # grounded pipeline. A daemon thread reads OpenRouter's SSE into a
        # queue; the drains below interleave its deltas as TEXT_MESSAGE_*
        # frames carrying the fixed `tourist` message id. Mock mode (no key)
        # never starts the lane and the tourist pane keeps the template.
        tourist_queue: queue.Queue = queue.Queue()
        tourist_state = {"started": False, "text": ""}
        words_display = (
            ", ".join(
                label
                for label in (labels or [])
                if isinstance(label, str) and label.strip()
            ).strip(", ")
            or "unspecified tastes"
        )
        tourist_thread: threading.Thread | None = None
        if os.environ.get("OPENROUTER_API_KEY"):

            def _tourist_worker() -> None:
                def on_delta(piece: str) -> None:
                    tourist_queue.put(piece)

                try:
                    _summarizer.stream_chat_deltas(
                        _tourist_lane_messages(city_display, words_display), on_delta
                    )
                except Exception:
                    pass
                finally:
                    tourist_queue.put(None)  # end-of-lane sentinel

            try:
                tourist_thread = threading.Thread(
                    target=_tourist_worker, name="tourist-lane", daemon=True
                )
                tourist_thread.start()
            except Exception:
                tourist_thread = None

        def _tourist_frames():
            """Drain queued generic-lane deltas as tourist TEXT_MESSAGE frames."""
            while True:
                try:
                    piece = tourist_queue.get_nowait()
                except queue.Empty:
                    return
                if piece is None:
                    continue
                # Models love markdown bold; the pane renders plain text.
                # Cleaned HERE so the emitted deltas and the accumulated
                # summary stay identical.
                cleaned = piece.replace("**", "").replace("* ", "")
                tourist_state["text"] += cleaned
                if not tourist_state["started"]:
                    tourist_state["started"] = True
                    yield sse_encode(text_start(TOURIST_MESSAGE_ID, thread_id, run_id))
                yield sse_encode(text_content(TOURIST_MESSAGE_ID, cleaned, thread_id, run_id))

        yield from _tourist_frames()

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
        scores = [
            _agent._entity_score(entity, rank, len(picked))
            for rank, entity in enumerate(picked)
        ]
        try:
            mid = float(median(scores)) if scores else 1.0
        except (ValueError, TypeError):
            mid = 1.0
        center = list(_agent.city_center(city_display) or [51.5074, -0.1278])
        seed_hint = next(
            (l for l in (labels or []) if isinstance(l, str) and l.strip()),
            city_display,
        )
        cards: list[dict] = []
        for index, entity in enumerate(picked):
            name = str(entity.get("name", ""))
            tags = entity.get("tags") if isinstance(entity.get("tags"), list) else []
            tag_hint = _agent._tag_label(tags[0]) if tags else (refs[0] if refs else "your crowd")
            tag_hint = tag_hint or (refs[0] if refs else "your crowd")
            latlng = _agent._entity_latlng(entity, index, center)
            cards.append(
                {
                    "name": name,
                    "address": _agent._entity_address(entity),
                    "latlng": latlng,
                    "why_you": f"Because {seed_hint} nights and {tag_hint} both live here",
                    "affinity_multiplier": _agent._affinity_multiplier(scores[index], mid),
                    "taste_path": [seed_hint, tag_hint, name],
                    "entity_id": str(entity.get("entity_id", "")),
                }
            )
        map_pins = [{"latlng": card["latlng"], "card_index": i} for i, card in enumerate(cards)]
        # Display center: mean over REAL entity locations only (Qloo geocodes
        # typed cities server-side, so even a city outside the dictionary
        # frames right); offset-derived or synthetic cards recenter exactly
        # onto the generation center instead.
        real_latlngs: list = []
        for entity in picked:
            location = entity.get("location") if isinstance(entity, dict) else None
            if not isinstance(location, dict):
                continue
            try:
                real_latlngs.append(
                    [float(location["lat"]), float(location.get("lon", location.get("lng")))]
                )
            except (KeyError, TypeError, ValueError):
                continue
        map_center = _agent.map_center_for(
            city_display, real_latlngs
        ) or list(center)
        try:
            _valid = _agent._valid_signals_for_insights(signals) if hasattr(_agent, "_valid_signals_for_insights") else (signals or None)
            _params = _agent.build_insights_params(city_display, _valid, take=8, refinements=refs)
            qloo_query = urllib.parse.urlencode(_params)
        except Exception:
            qloo_query = (
                "filter.type=urn:entity:place&filter.location.query="
                + urllib.parse.quote(city_display, safe="")
                + "&feature.explainability=true&take=8"
            )
        taste_path_sentence = ""
        try:
            _first_path = cards[0].get("taste_path") if cards else None
            if isinstance(_first_path, list):
                # Drop raw entity IDs (bare UUIDs leak via seed_hint fallbacks).
                _cleaned = [
                    str(p) for p in _first_path
                    if isinstance(p, str) and p.strip() and not _agent.is_entity_id(p.strip())
                ]
                if len(_cleaned) >= 2:
                    taste_path_sentence = " \u2192 ".join(_cleaned)
        except Exception:
            taste_path_sentence = ""
        if not taste_path_sentence:
            try:
                _head = [str(entry) for entry in (labels or []) if isinstance(entry, str) and str(entry).strip()][:2]
                _tail = str(cards[0].get("name", city_display)) if cards else city_display
                taste_path_sentence = " \u2192 ".join(_head + [_tail]) if _head else f"{city_display} \u2192 your crowd \u2192 {_tail}"
            except Exception:
                taste_path_sentence = f"{city_display} \u2192 your crowd \u2192 {city_display}"
        try:
            model_slug = _agent._model_slug()
        except Exception:
            model_slug = "deepseek/deepseek-v4.1-flash"
        why_qloo = {
            "taste_path_sentence": taste_path_sentence,
            "qloo_query": qloo_query,
            "model": model_slug,
        }
        yield sse_encode(
            state_delta(
                {"cards": cards, "pins": map_pins, "map_pins": map_pins, "map_center": map_center, "broadened": broadened, "qloo_query": qloo_query, "taste_path_sentence": taste_path_sentence, "model": model_slug, "why_qloo": why_qloo},
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
        yield from _tourist_frames()

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
        # The insights+brand hops gave the generic lane the whole pipeline to
        # stream into; close it out (bounded) before the grounded panes land,
        # draining continuously so slow-first-token streams still arrive.
        if tourist_thread is not None and tourist_thread.is_alive():
            deadline = time.monotonic() + _TOURIST_JOIN_TIMEOUT
            while tourist_thread.is_alive() and time.monotonic() < deadline:
                yield from _tourist_frames()
                tourist_thread.join(timeout=0.5)
        yield from _tourist_frames()
        if tourist_state["started"]:
            yield sse_encode(text_end(TOURIST_MESSAGE_ID, thread_id, run_id))
        streamed_tourist = tourist_state["text"].strip()
        tourist_items: list[str] = []
        if streamed_tourist:
            tourist_summary = streamed_tourist
            for line in streamed_tourist.splitlines():
                cleaned = re.sub(r"^\s*(?:[-\u2022*]|\d+[.)])\s*", "", line).strip()
                if cleaned:
                    tourist_items.append(cleaned[:200])
            if len(tourist_items) < 2:
                tourist_items = []  # a paragraph, not a list — keep template rows
            tourist_items = tourist_items[:5]
        try:
            panes = _agent._summarize_fn()(labels or [city_display], {"cards": picked, "city": city_display})
            if isinstance(panes, dict):
                tourist = panes.get("tourist") if isinstance(panes.get("tourist"), dict) else {}
                transplant = panes.get("transplant") if isinstance(panes.get("transplant"), dict) else {}
            if isinstance(tourist.get("summary"), str) and tourist["summary"].strip() and not streamed_tourist:
                tourist_summary = tourist["summary"]
                if isinstance(transplant.get("summary"), str) and transplant["summary"].strip():
                    transplant_summary = transplant["summary"]
                overrides = transplant.get("cards") if isinstance(transplant, dict) else []
                if isinstance(overrides, list):
                    by_name: dict[str, str] = {}
                    for c in overrides:
                        try:
                            if not isinstance(c, dict):
                                continue
                            raw_name = c.get("name")
                            raw_line = c.get("why_you")
                            if not isinstance(raw_name, str) or not isinstance(raw_line, str):
                                continue
                            name = raw_name.strip()
                            line = raw_line.strip()
                            if not name or not line:
                                continue
                            by_name[name] = line[:200]
                        except Exception:
                            continue
                    for card in cards:
                        try:
                            if card.get("name") in by_name:
                                card["why_you"] = by_name[card["name"]]
                        except Exception:
                            continue
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
                    "tourist": {"summary": tourist_summary, "items": tourist_items or default_tourist_items(city_display)},
                    "transplant": {"summary": transplant_summary},
                    "tourist_summary": tourist_summary,
                    "tourist_items": tourist_items,
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
                    "confirmations": confirmations,
                    "pins": map_pins,
                    "map_pins": map_pins,
                    "map_center": map_center,
                    "brands": brands,
                    "tourist": {"summary": tourist_summary, "items": tourist_items or default_tourist_items(city_display)},
                    "tourist_summary": tourist_summary,
                    "tourist_items": tourist_items,
                    "transplant_summary": transplant_summary,
                    "city": city_display,
                    "broadened": broadened,
                    "qloo_query": qloo_query,
                    "taste_path_sentence": taste_path_sentence,
                    "model": model_slug,
                    "why_qloo": why_qloo,
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
