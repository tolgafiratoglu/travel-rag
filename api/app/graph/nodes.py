import json
import re

from app.graph.state import GraphState
from app.services import llm

PLANNER_PROMPT = """You are a travel assistant planner. Analyze the user's question and return ONLY valid JSON (no markdown fences or extra commentary).

Schema:
{{
  "intent": "chitchat|factoid|recommendation|planning|comparison|logistics|follow_up|meta",
  "entities": {{
    "cities": [],
    "countries": [],
    "theme": [],
    "audience": [],
    "budget": null,
    "duration": null
  }},
  "assumptions": [],
  "plan": {{
    "summary": "1-2 sentences for the user: what you will research, in the user's language",
    "steps": [
      {{ "id": "s1", "label": "Search relevant sources", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Prepare the answer", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["retrieval-optimized query 1", "query 2"],
  "answer_style": "recommendation|factoid|itinerary|comparison|chitchat"
}}

Rules:
- Write summary and assumptions in the user's language (match the question language)
- If information is missing, add soft fallbacks to assumptions[]; do not ask the user clarifying questions
- search_queries: 1-3 short phrases suitable for vector search
- steps must always include s1 (vector) and s2 (synthesize)

### Question
{question}

### JSON
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in planner response")
    return json.loads(text[start : end + 1])


async def intake_node(state: GraphState) -> GraphState:
    question = (state.get("question") or "").strip()
    if not question:
        return {"error": "Empty question"}
    return {
        "question": question,
        "history": state.get("history") or [],
    }


async def planner_json_node(state: GraphState) -> GraphState:
    if state.get("error"):
        return {}

    prompt = PLANNER_PROMPT.format(question=state["question"])
    raw = await llm.generate(prompt)

    try:
        data = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        return {
            "planner_raw": raw,
            "error": f"Planner JSON parse failed: {e}",
        }

    plan = data.get("plan") or {}
    if "summary" not in plan or "steps" not in plan:
        return {
            "planner_raw": raw,
            "error": "Planner JSON missing plan.summary or plan.steps",
        }

    return {
        "intent": data.get("intent", "factoid"),
        "entities": data.get("entities") or {},
        "assumptions": data.get("assumptions") or [],
        "plan": plan,
        "search_queries": data.get("search_queries") or [],
        "answer_style": data.get("answer_style", ""),
        "planner_raw": raw,
    }
