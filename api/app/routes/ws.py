import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.graph.builder import build_graph

logger = logging.getLogger(__name__)

router = APIRouter()
_graph = build_graph()


async def _send(ws: WebSocket, event: dict) -> None:
    await ws.send_text(json.dumps(event, ensure_ascii=False))


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    try:
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

                    intent = update.get("intent", "")
                    first_kind = "synthesize" if intent in ("chitchat", "meta") else "vector"
                    await _send(ws, {"type": "step", "kind": first_kind, "status": "running"})

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
            "assumptions": result.get("assumptions") or [],
            "intent": result.get("intent"),
            "prompt_language": result.get("prompt_language"),
            "search_queries": result.get("search_queries") or [],
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
