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
                    m_count = len(update.get("museum_chunks") or [])
                    a_count = len(update.get("artifact_chunks") or [])
                    if result.get("search_museums"):
                        await _send(ws, {
                            "type": "step",
                            "kind": "museums",
                            "status": "done",
                            "detail": _museums_detail(m_count, lang),
                        })
                    if result.get("search_artifacts"):
                        await _send(ws, {
                            "type": "step",
                            "kind": "artifacts",
                            "status": "done",
                            "detail": _artifacts_detail(a_count, lang),
                        })
                    await _send(ws, {"type": "step", "kind": "vector", "status": "running"})

                elif node_name == "vector_retrieval":
                    count = len(update.get("vector_chunks") or [])
                    await _send(ws, {
                        "type": "step",
                        "kind": "vector",
                        "status": "done",
                        "detail": _vector_detail(count, lang),
                    })
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
