import json
import logging
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.graph.builder import build_graph
from app.services.rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter()
_graph = build_graph()

_TR_CHARS = re.compile(r"[ıİşŞğĞüÜöÖçÇ]")


def _lang_code(raw: str | None) -> str:
    code = (raw or "en").strip().lower().split("-")[0]
    return code if code in ("tr", "en") else "en"


def _guess_lang_from_text(text: str) -> str:
    return "tr" if _TR_CHARS.search(text or "") else "en"


def _status_message(kind: str, lang: str) -> str:
    messages = {
        "thinking": {"en": "Thinking…", "tr": "Düşünüyor…"},
        "answering": {"en": "Answering your question…", "tr": "Sorunuzu yanıtlıyorum…"},
    }
    bucket = messages.get(kind, messages["thinking"])
    return bucket.get(lang) or bucket["en"]


def _chunk_city(chunk: dict) -> str:
    """City used for per-step counters (query pin, else payload)."""
    return (chunk.get("query_city") or chunk.get("city_name") or "").strip()


def _counts_by_city(
    chunks: list[dict],
    source_kind: str | None = None,
) -> dict[str, int]:
    """Count candidates per city, optionally filtered by ``source_kind``."""
    out: dict[str, int] = {}
    for chunk in chunks:
        if source_kind and chunk.get("source_kind") != source_kind:
            continue
        city = _chunk_city(chunk)
        out[city] = out.get(city, 0) + 1
    return out


def _cities_from_queries(queries: list | None) -> list[str]:
    """Preserve planner city order for assigning leftover step counts."""
    seen: list[str] = []
    for item in queries or []:
        if isinstance(item, dict):
            city = (item.get("city") or "").strip()
        else:
            city = (getattr(item, "city", None) or "").strip()
        if not city or city.upper() == "ANY":
            continue
        if all(city.casefold() != s.casefold() for s in seen):
            seen.append(city)
    return seen


def _plan_steps(result: dict) -> list[dict]:
    plan = result.get("plan") or {}
    steps = plan.get("steps") or []
    return [s for s in steps if isinstance(s, dict)]


def _assign_counts_to_steps(
    steps: list[dict],
    kind: str,
    by_city: dict[str, int],
    preferred_cities: list[str] | None = None,
) -> list[tuple[str, int]]:
    """Map retrieval counts onto plan steps of ``kind``.

    One step → total. Several steps → match city names in labels, then assign
    leftover cities in planner query order so comparison UIs stay honest.
    """
    kind_steps = [s for s in steps if s.get("kind") == kind and s.get("id")]
    if not kind_steps:
        return []

    total = sum(by_city.values())
    if len(kind_steps) == 1:
        return [(str(kind_steps[0]["id"]), total)]

    remaining = dict(by_city)
    assigned: dict[str, int] = {}
    unmatched: list[dict] = []

    for step in kind_steps:
        label = (step.get("label") or "").casefold()
        step_id = str(step["id"])
        matched: str | None = None
        for city in sorted(remaining.keys(), key=len, reverse=True):
            if city and city.casefold() in label:
                matched = city
                break
        if matched is not None:
            assigned[step_id] = remaining.pop(matched)
        else:
            unmatched.append(step)

    leftover: list[str] = []
    if preferred_cities:
        for pref in preferred_cities:
            key = next(
                (k for k in remaining if k.casefold() == pref.casefold()),
                None,
            )
            if key is not None and key not in leftover:
                leftover.append(key)
        for key in remaining:
            if key not in leftover:
                leftover.append(key)
    else:
        leftover = list(remaining.keys())

    for i, step in enumerate(unmatched):
        step_id = str(step["id"])
        if i < len(leftover):
            city = leftover[i]
            assigned[step_id] = remaining.pop(city, 0)
        else:
            assigned[step_id] = 0

    return [(str(s["id"]), assigned.get(str(s["id"]), 0)) for s in kind_steps]


def _graph_detail(count: int, lang: str) -> str:
    if lang == "tr":
        return f"{count} graf sonucu"
    return f"{count} graph result{'s' if count != 1 else ''}"


def _vector_detail(count: int, lang: str) -> str:
    if lang == "tr":
        return f"{count} kaynak bulundu"
    return f"{count} source{'s' if count != 1 else ''} found"


def _museums_detail(count: int, lang: str) -> str:
    if lang == "tr":
        return f"{count} müze kaynağı"
    return f"{count} museum source{'s' if count != 1 else ''}"


def _artifacts_detail(count: int, lang: str) -> str:
    if lang == "tr":
        return f"{count} tarihi kaynak"
    return f"{count} historical source{'s' if count != 1 else ''}"


async def _send(ws: WebSocket, event: dict) -> None:
    await ws.send_text(json.dumps(event, ensure_ascii=False))


async def _send_step_details(
    ws: WebSocket,
    *,
    result: dict,
    kind: str,
    by_city: dict[str, int],
    preferred_cities: list[str] | None,
    detail_fn,
    lang: str,
) -> None:
    """Emit done events with per-step counts when the plan has multiple rows."""
    assignments = _assign_counts_to_steps(
        _plan_steps(result), kind, by_city, preferred_cities
    )
    if not assignments:
        await _send(
            ws,
            {
                "type": "step",
                "kind": kind,
                "status": "done",
                "detail": detail_fn(sum(by_city.values()), lang),
            },
        )
        return

    for step_id, count in assignments:
        await _send(
            ws,
            {
                "type": "step",
                "kind": kind,
                "step_id": step_id,
                "status": "done",
                "detail": detail_fn(count, lang),
            },
        )


def _client_key(ws: WebSocket) -> str:
    forwarded = ws.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if ws.client and ws.client.host:
        return ws.client.host
    return "unknown"


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    try:
        if settings.rate_limit_enabled:
            decision = get_rate_limiter().check(_client_key(ws))
            if not decision.allowed:
                await _send(
                    ws,
                    {
                        "type": "error",
                        "message": (
                            "Too many requests. Try again in "
                            f"{decision.retry_after_seconds}s."
                        ),
                    },
                )
                await ws.close()
                return

        raw = await ws.receive_text()
        msg = json.loads(raw)
        question = (msg.get("content") or "").strip()
        history = msg.get("history") or []

        if not question:
            await _send(ws, {"type": "error", "message": "Empty question"})
            await ws.close()
            return

        ui_lang = _guess_lang_from_text(question)
        await _send(
            ws,
            {"type": "status", "message": _status_message("thinking", ui_lang)},
        )

        result: dict = {}

        async for mode, chunk in _graph.astream(
            {"question": question, "history": history},
            stream_mode=["updates", "custom"],
        ):
            if mode == "custom":
                if isinstance(chunk, dict) and chunk.get("type") == "token":
                    delta = chunk.get("delta") or ""
                    if delta:
                        await _send(ws, {"type": "token", "delta": delta})
                continue

            if mode != "updates" or not isinstance(chunk, dict):
                continue

            for node_name, update in chunk.items():
                if not update:
                    continue
                result.update(update)
                lang = _lang_code(result.get("prompt_language")) or ui_lang

                if node_name == "safety_guard":
                    if update.get("error"):
                        await _send(ws, {"type": "error", "message": update["error"]})
                        await ws.close()
                        return

                if node_name == "planner_json":
                    if update.get("error"):
                        await _send(ws, {"type": "error", "message": update["error"]})
                        await ws.close()
                        return

                    lang = _lang_code(update.get("prompt_language")) or ui_lang
                    plan = update.get("plan") or {}
                    await _send(ws, {
                        "type": "plan",
                        "summary": plan.get("summary", ""),
                        "steps": plan.get("steps", []),
                        "prompt_language": update.get("prompt_language") or lang,
                    })

                    primary_intent = update.get("primary_intent", "")
                    if update.get("in_scope") is False:
                        first_kind = "synthesize"
                    elif update.get("needs_clarification"):
                        first_kind = "synthesize"
                    elif primary_intent in ("chitchat", "meta"):
                        first_kind = "synthesize"
                    elif update.get("needs_graph"):
                        first_kind = "graph"
                    elif update.get("deep_search"):
                        first_kind = "museums"
                    else:
                        first_kind = "vector"
                    await _send(ws, {"type": "step", "kind": first_kind, "status": "running"})

                elif node_name == "graph_retrieval":
                    count = len(update.get("graph_contexts") or [])
                    await _send(ws, {
                        "type": "step",
                        "kind": "graph",
                        "status": "done",
                        "detail": _graph_detail(count, lang),
                    })
                    deep = bool(result.get("deep_search"))
                    await _send(
                        ws,
                        {
                            "type": "step",
                            "kind": "museums" if deep else "vector",
                            "status": "running",
                        },
                    )

                elif node_name == "deep_retrieval":
                    chunks = update.get("candidate_chunks") or []
                    if result.get("search_museums"):
                        await _send_step_details(
                            ws,
                            result=result,
                            kind="museums",
                            by_city=_counts_by_city(chunks, "museum"),
                            preferred_cities=_cities_from_queries(
                                result.get("museum_search_queries")
                            ),
                            detail_fn=_museums_detail,
                            lang=lang,
                        )
                    if result.get("search_artifacts"):
                        await _send_step_details(
                            ws,
                            result=result,
                            kind="artifacts",
                            by_city=_counts_by_city(chunks, "historical_site"),
                            preferred_cities=_cities_from_queries(
                                result.get("artifact_search_queries")
                            ),
                            detail_fn=_artifacts_detail,
                            lang=lang,
                        )
                    await _send(ws, {"type": "step", "kind": "vector", "status": "running"})

                elif node_name == "vector_retrieval":
                    chunks = update.get("candidate_chunks") or []
                    await _send_step_details(
                        ws,
                        result=result,
                        kind="vector",
                        by_city=_counts_by_city(chunks),
                        preferred_cities=_cities_from_queries(
                            result.get("search_queries")
                        ),
                        detail_fn=_vector_detail,
                        lang=lang,
                    )
                    await _send(
                        ws,
                        {
                            "type": "step",
                            "kind": "synthesize",
                            "status": "running",
                            "status_message": _status_message("answering", lang),
                        },
                    )

                elif node_name in ("synthesize", "chitchat", "out_of_scope", "clarify"):
                    await _send(ws, {"type": "step", "kind": "synthesize", "status": "done"})

        if result.get("error"):
            await _send(ws, {"type": "error", "message": result["error"]})
            await ws.close()
            return

        await _send(ws, {
            "type": "done",
            "answer": result.get("answer", ""),
            "sources": result.get("sources") or [],
            "plan": result.get("plan") or {},
            "primary_intent": result.get("primary_intent"),
            "in_scope": result.get("in_scope", True),
            "needs_clarification": result.get("needs_clarification", False),
            "suggestions": result.get("suggestions") or [],
            "intents": result.get("intents") or [],
            "prompt_language": result.get("prompt_language"),
            "search_queries": result.get("search_queries") or [],
            "deep_search": result.get("deep_search", False),
            "search_museums": result.get("search_museums", False),
            "search_artifacts": result.get("search_artifacts", False),
            "museum_search_queries": result.get("museum_search_queries") or [],
            "artifact_search_queries": result.get("artifact_search_queries") or [],
            "is_hotel_search": result.get("is_hotel_search", False),
            "hotel_search_query": result.get("hotel_search_query", ""),
            "is_flight_search": result.get("is_flight_search", False),
            "flight_search_query": result.get("flight_search_query", ""),
            "needs_graph": result.get("needs_graph", False),
            "graph_queries": result.get("graph_queries") or [],
        })
        await ws.close()

    except WebSocketDisconnect:
        logger.debug("Client disconnected")
    except Exception as e:
        logger.exception("WS error")
        try:
            await _send(ws, {"type": "error", "message": str(e)})
            await ws.close()
        except Exception:
            pass
