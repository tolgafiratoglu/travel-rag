import json
import logging
import re

from app.graph.state import GraphState
from app.services import graph_store
from app.services import llm
from app.services import vector_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

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
    "summary": "One short active sentence in the user's language: what you are about to do (e.g. 'Looking into the best season for Goreme.')",
    "steps": [
      {{ "id": "s1", "label": "descriptive action", "kind": "vector|synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["retrieval-optimized query 1", "query 2"],
  "answer_style": "recommendation|factoid|itinerary|comparison|chitchat"
}}

Rules:
- summary: One short, active sentence in the user's language (e.g. "Looking into..."). Keep it under 15 words.
- assumptions: Write in the user's language; if information is missing, add soft fallbacks here instead of asking
- steps: Generate 2-5 steps tailored to the question. Each step needs a unique id (s1, s2, ...), a descriptive label in the user's language, and a kind. The last step must always be kind "synthesize". All other steps should be kind "vector". Vary the number, labels, and order based on what the question actually needs.
- For chitchat or meta intents: use a single step with kind "synthesize" and set search_queries to []
- search_queries: 1-3 short phrases suitable for vector search (empty for chitchat/meta)
- All steps start with status "idle"

Example — question: "Where can I go with my family in Antalya?"
{{
  "intent": "recommendation",
  "plan": {{
    "summary": "Searching for family-friendly places in Antalya.",
    "steps": [
      {{ "id": "s1", "label": "Search family-friendly spots in Antalya", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Look up kid-friendly beaches and activities", "kind": "vector", "status": "idle" }},
      {{ "id": "s3", "label": "Compile and present recommendations", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["Antalya family friendly places", "Antalya kid friendly beach activities"]
}}

Example — question: "Hello, what can you do?"
{{
  "intent": "chitchat",
  "plan": {{
    "summary": "Answering your question.",
    "steps": [
      {{ "id": "s1", "label": "Reply to your message", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": []
}}

### Question
{question}

### JSON
"""

SYNTHESIZE_PROMPT = """You are a travel assistant. Answer the user's question using ONLY the provided sources. Reply in the user's language.

Reference vector sources as [S1], [S2], etc. Reference graph profiles as [G1], [G2], etc.

If the sources don't contain enough information, say so honestly. Do not make up information.

{vector_section}{graph_section}### Question
{question}

### Answer
"""

SYNTHESIZE_VECTOR_SECTION = """### Vector sources
{vector_sources}

"""

SYNTHESIZE_GRAPH_SECTION = """### Graph knowledge
{graph_sources}

"""

CHITCHAT_PROMPT = """You are a friendly travel assistant. The user is making casual conversation or asking about your capabilities. Reply naturally in the user's language. Keep it brief and warm.

### Message
{question}

### Reply
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


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


async def vector_retrieval_node(state: GraphState) -> GraphState:
    queries = state.get("search_queries") or []
    if not queries:
        return {"vector_chunks": [], "sources": []}

    all_chunks: list[dict] = []
    seen: set[tuple] = set()

    for q in queries:
        try:
            results = await vector_store.search(q, top_k=5)
        except Exception as e:
            logger.warning("Vector search failed for %r: %s", q, e)
            continue
        for chunk in results:
            key = (chunk["city_name"], chunk["section"], chunk["text"][:100])
            if key not in seen:
                seen.add(key)
                all_chunks.append(chunk)

    all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
    all_chunks = all_chunks[:10]

    return {
        "vector_chunks": all_chunks,
        "sources": all_chunks,
    }


async def graph_retrieval_node(state: GraphState) -> GraphState:
    cities: list[str] = list((state.get("entities") or {}).get("cities") or [])
    if not cities:
        return {"graph_contexts": []}

    try:
        contexts = await graph_store.fetch_contexts_for_cities(cities)
    except Exception as e:
        logger.warning("Graph retrieval failed: %s", e)
        contexts = []

    return {"graph_contexts": contexts}


async def chitchat_node(state: GraphState) -> GraphState:
    prompt = CHITCHAT_PROMPT.format(question=state["question"])
    answer = await llm.generate(prompt)
    return {"answer": answer, "sources": []}


async def synthesize_node(state: GraphState) -> GraphState:
    chunks = state.get("vector_chunks") or []
    contexts = state.get("graph_contexts") or []

    if not chunks and not contexts:
        return {
            "answer": "I couldn't find relevant information in my sources. "
            "Try rephrasing your question or asking about a specific city.",
        }

    vector_section = ""
    if chunks:
        vector_sources = "\n\n".join(
            f"[S{i + 1}] ({c['city_name']} / {c['section']}): {c['text']}"
            for i, c in enumerate(chunks)
        )
        vector_section = SYNTHESIZE_VECTOR_SECTION.format(vector_sources=vector_sources)

    graph_section = ""
    if contexts:
        graph_sources = "\n\n".join(
            f"[G{i + 1}] ({ctx['city']}):\n{ctx['text']}"
            for i, ctx in enumerate(contexts)
        )
        graph_section = SYNTHESIZE_GRAPH_SECTION.format(graph_sources=graph_sources)

    prompt = SYNTHESIZE_PROMPT.format(
        vector_section=vector_section,
        graph_section=graph_section,
        question=state["question"],
    )
    answer = await llm.generate(prompt)
    return {"answer": answer}


async def format_node(state: GraphState) -> GraphState:
    """Prepare final response. Currently a pass-through for future post-processing."""
    return {}
