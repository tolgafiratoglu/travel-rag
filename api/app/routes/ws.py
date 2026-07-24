import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.graph.builder import build_graph
from app.services.rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter()
_graph = build_graph()


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

        await _send(ws, {"type": "status", "message": "Answering your question..."})

        result: dict = {}

        async for chunk in _graph.astream(
            {"question": question, "history": history},
            stream_mode="updates",
        ):
            for node_name, update in chunk.items():
                if not update:
                    continue
                result.update(update)

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

                    plan = update.get("plan") or {}
                    await _send(ws, {
                        "type": "plan",
                        "summary": plan.get("summary", ""),
                        "steps": plan.get("steps", []),
                    })

                    primary_intent = update.get("primary_intent", "")
                    if primary_intent in ("chitchat", "meta"):
                        first_kind = "synthesize"
                    elif update.get("deep_search"):
                        first_kind = "museums"
                    else:
                        first_kind = "vector"
                    await _send(ws, {"type": "step", "kind": first_kind, "status": "running"})

                elif node_name == "deep_retrieval":
                    m_count = len(update.get("museum_chunks") or [])
                    a_count = len(update.get("artifact_chunks") or [])
                    await _send(ws, {
                        "type": "step",
                        "kind": "museums",
                        "status": "done",
                        "detail": f"{m_count} museum, {a_count} historical sources",
                    })
                    await _send(ws, {"type": "step", "kind": "vector", "status": "running"})

                elif node_name == "vector_retrieval":
                    count = len(update.get("vector_chunks") or [])
                    await _send(ws, {
                        "type": "step",
                        "kind": "vector",
                        "status": "done",
                        "detail": f"{count} sources found",
                    })
                    await _send(ws, {"type": "step", "kind": "graph", "status": "running"})

                elif node_name == "graph_retrieval":
                    count = len(update.get("graph_contexts") or [])
                    await _send(ws, {
                        "type": "step",
                        "kind": "graph",
                        "status": "done",
                        "detail": f"{count} city profiles found",
                    })
                    await _send(ws, {"type": "step", "kind": "synthesize", "status": "running"})

                elif node_name in ("synthesize", "chitchat"):
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
            "neo4j_data": result.get("neo4j_data") or {},
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
