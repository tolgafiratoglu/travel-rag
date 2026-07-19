import json
import logging
import re

from pydantic import ValidationError

from app.graph.state import GraphState
from app.models.planner import cities_from_intents, parse_planner_output, planner_state_update
from app.services import graph_store
from app.services import llm
from app.services import vector_store

logger = logging.getLogger(__name__)

# TODO: remove before prod — appends LLM prompts to the answer for debugging
DEBUG_APPEND_PROMPT = True

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PLANNER_PROMPT = """You are a travel assistant planner. Analyze the user's question and return ONLY valid JSON (no markdown fences or extra commentary).

Schema:
{{
  "primary_intent": "chitchat|factoid|recommendation|planning|comparison|logistics|meta",
  "intents": [
    {{ "intent": "planning", "cities": ["Antalya"] }}
  ],
  "prompt_language": "ISO 639-1 code of the user's message (e.g. tr, en, de)",
  "plan": {{
    "summary": "One short active sentence in the user's language describing what you will do next",
    "steps": [
      {{ "id": "s1", "label": "descriptive action in the user's language", "kind": "vector|graph|external|synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["retrieval-optimized phrase 1", "phrase 2"],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "neo4j_data": {{}}
}}

Rules:
- prompt_language: detect the language of the user's message; use ISO 639-1 (tr, en, de, fr, ...).
- summary and step labels: write in the user's language (prompt_language). Keep summary under 15 words.
- primary_intent: the single main goal of the question.
- intents: zero or more task slots. Order does not matter. Each item is {{ "intent": "...", "cities": [...] }}.
  Allowed intents[].intent values: factoid, recommendation, planning, comparison, logistics.
  Do NOT put chitchat or meta inside intents[]; those are primary_intent only (use intents: []).
  cities: cities relevant to that specific intent (empty list if none).
  If the user mixes goals (e.g. plan + compare), emit multiple intents entries with cities scoped per intent.
  When primary_intent is a task intent (not chitchat/meta), include a matching entry in intents[].
- steps: generate 2-5 steps tailored to the question. Each step needs a unique id (s1, s2, ...), a descriptive label, and a kind. The last step must always be kind "synthesize". Use kind "vector" for Wikivoyage/source retrieval, "graph" when neo4j_data is non-empty, "external" when hotel or flight search links are prepared. All steps start with status "idle".
- For chitchat or meta primary_intent: use a single synthesize step and set intents to [], search_queries to [], neo4j_data to {{}}, is_hotel_search and is_flight_search to false.
- search_queries: 1-3 short English phrases optimized for vector search over travel guide content (empty for chitchat/meta). These are separate from hotel/flight URLs.

Hotel search (Booking.com):
- is_hotel_search: true only when the user asks for accommodation, hotels, or lodging.
- hotel_search_query: when is_hotel_search is true, build a full Booking.com URL using only known parameters. Omit any parameter you cannot infer — do not guess dates or locations.
  Base: https://www.booking.com/searchresults.html
  Parameters: ss (city/area, required for hotel search), checkin (YYYY-MM-DD), checkout (YYYY-MM-DD)
  Example with dates: https://www.booking.com/searchresults.html?ss=Antalya&checkin=2026-08-10&checkout=2026-08-15
  Example without dates: https://www.booking.com/searchresults.html?ss=Antalya
- When is_hotel_search is false, set hotel_search_query to "".

Flight search (Kayak):
- is_flight_search: true only when the user asks for flights or airfare.
- flight_search_query: when is_flight_search is true, build a full Kayak URL using only known parameters. Prefer IATA airport codes (Istanbul→IST, Antalya→AYT). Omit unknown segments — do not guess dates or routes.
  Format: https://www.kayak.com/flights/{{ORIGIN}}-{{DEST}}/{{DEPART}}/{{RETURN}}?sort=bestflight_a
  One-way (no return date): https://www.kayak.com/flights/IST-AYT/2026-08-10?sort=bestflight_a
  Round-trip: https://www.kayak.com/flights/IST-AYT/2026-08-10/2026-08-15?sort=bestflight_a
  No dates: https://www.kayak.com/flights/IST-AYT?sort=bestflight_a
- When is_flight_search is false, set flight_search_query to "".

neo4j_data:
- A dict mapping entity names to graph relation types. Use only when graph knowledge would help answer the question.
- Keys: city, country, or place names mentioned in the question.
- Values: exactly one of: Country, Region, City, District, Attraction, Activity, Airport, TransportHub, Route, TravelTheme, Season, Budget
- Examples: {{"Istanbul": "City"}}, {{"Istanbul": "Airport"}}, {{"Cappadocia": "Region", "Goreme": "Season"}}
- Use "City" for general city profile questions; "Airport" or "TransportHub" for airport/transport access; "Season" for best time to visit; "Attraction" for sights/UNESCO.
- When graph data is not needed, set neo4j_data to {{}}.

Example — question: "Where can I go with my family in Antalya?"
{{
  "primary_intent": "recommendation",
  "intents": [
    {{ "intent": "recommendation", "cities": ["Antalya"] }}
  ],
  "prompt_language": "en",
  "plan": {{
    "summary": "Searching for family-friendly places in Antalya.",
    "steps": [
      {{ "id": "s1", "label": "Search family-friendly spots in Antalya", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Compile and present recommendations", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["Antalya family friendly places", "Antalya kid friendly beach activities"],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "neo4j_data": {{ "Antalya": "City" }}
}}

Example — question: "Antalya'da 5 gün planla, Bodrum ile karşılaştır"
{{
  "primary_intent": "planning",
  "intents": [
    {{ "intent": "planning", "cities": ["Antalya"] }},
    {{ "intent": "comparison", "cities": ["Antalya", "Bodrum"] }}
  ],
  "prompt_language": "tr",
  "plan": {{
    "summary": "Antalya planı hazırlayıp Bodrum ile karşılaştırıyorum.",
    "steps": [
      {{ "id": "s1", "label": "Antalya ve Bodrum bilgilerini araştır", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Plan ve karşılaştırmayı sentezle", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["Antalya 5 day itinerary", "Bodrum vs Antalya"],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "neo4j_data": {{ "Antalya": "City", "Bodrum": "City" }}
}}

Example — question: "İstanbul'da 5 günlük rota, otel ve uçuş öner"
{{
  "primary_intent": "planning",
  "intents": [
    {{ "intent": "planning", "cities": ["İstanbul"] }},
    {{ "intent": "logistics", "cities": ["İstanbul"] }}
  ],
  "prompt_language": "tr",
  "plan": {{
    "summary": "İstanbul için 5 günlük rota, otel ve uçuş seçeneklerini hazırlıyorum.",
    "steps": [
      {{ "id": "s1", "label": "İstanbul gezi noktalarını araştır", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Konaklama ve uçuş bağlantılarını hazırla", "kind": "external", "status": "idle" }},
      {{ "id": "s3", "label": "Rota önerisini sentezle", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": ["Istanbul 5 day itinerary", "Istanbul top attractions"],
  "is_hotel_search": true,
  "hotel_search_query": "https://www.booking.com/searchresults.html?ss=İstanbul",
  "is_flight_search": true,
  "flight_search_query": "https://www.kayak.com/flights/IST-IST?sort=bestflight_a",
  "neo4j_data": {{ "İstanbul": "City" }}
}}

Example — question: "Hello, what can you do?"
{{
  "primary_intent": "chitchat",
  "intents": [],
  "prompt_language": "en",
  "plan": {{
    "summary": "Answering your question.",
    "steps": [
      {{ "id": "s1", "label": "Reply to your message", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "neo4j_data": {{}}
}}

### Question
{question}

### JSON
"""

GROUNDING_POLICY = """Grounding and partial-answer policy:
Use only the provided sources for factual claims. Vector sources [S] and graph profiles [G] both count as sources.

First identify the user's core request and any modifiers or constraints.

If the provided sources are irrelevant to the core request, say that the answer is not available in the provided sources.

If the sources support a material part of the core request but do not fully answer the question, give a partial answer:
- state what the sources do support,
- cite the supporting sources,
- clearly state what is missing or not explicitly covered,
- do not fill missing details with assumptions.

If the sources support related information but not the exact requested attribute, present it as limited context, not as a direct answer.

Use phrasing in the user's language, for example:
"Sources do not fully answer this question, but they support the following..."
or
"X is not explicitly stated in the sources, but Y is available..."

Do not infer labels such as family-friendly, cheap, safe, romantic, accessible, best, or suitable unless the sources explicitly support them.

Reject completely only when no source supports any material part of the user's core request."""

SYNTHESIZE_PROMPT = """You are a travel assistant. Answer the user's question using the provided sources. Reply in the user's language.

Reference vector sources as [S1], [S2], etc. Reference graph profiles as [G1], [G2], etc.

{grounding_policy}

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

SYNTHESIZE_EXTERNAL_SECTION = """### External booking links
Include these links in your answer when relevant (use markdown links with descriptive labels in the user's language):
{external_links}

"""

CHITCHAT_PROMPT = """You are a friendly travel assistant. The user is making casual conversation or asking about your capabilities. Reply naturally in the user's language. Keep it brief and warm.

### Message
{question}

### Reply
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_debug_footer(answer: str, state: GraphState, prompt: str) -> str:
    if not DEBUG_APPEND_PROMPT:
        return answer
    footer = f"\n\n----- prompt -----\n{prompt}"
    planner_raw = state.get("planner_raw")
    if planner_raw:
        footer = f"\n\n----- planner response -----\n{planner_raw}" + footer
    return answer + footer


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
        planner = parse_planner_output(data)
    except (json.JSONDecodeError, ValueError) as e:
        return {
            "planner_raw": raw,
            "error": f"Planner JSON parse failed: {e}",
        }
    except ValidationError as e:
        return {
            "planner_raw": raw,
            "error": f"Planner validation failed: {e}",
        }

    return planner_state_update(planner, raw)


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
    neo4j_data = state.get("neo4j_data") or {}
    if neo4j_data:
        cities = list(neo4j_data.keys())
    else:
        cities = cities_from_intents(state.get("intents"))

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
    return {"answer": _append_debug_footer(answer, state, prompt), "sources": []}


def _build_external_links_section(state: GraphState) -> str:
    links: list[str] = []
    if state.get("is_hotel_search") and state.get("hotel_search_query"):
        links.append(f"Hotel: {state['hotel_search_query']}")
    if state.get("is_flight_search") and state.get("flight_search_query"):
        links.append(f"Flights: {state['flight_search_query']}")
    if not links:
        return ""
    return SYNTHESIZE_EXTERNAL_SECTION.format(external_links="\n".join(links))


async def synthesize_node(state: GraphState) -> GraphState:
    chunks = state.get("vector_chunks") or []
    contexts = state.get("graph_contexts") or []
    external_section = _build_external_links_section(state)
    has_external_only = bool(external_section) and not chunks and not contexts

    if not chunks and not contexts and not has_external_only:
        answer = (
            "I couldn't find relevant information in my sources. "
            "Try rephrasing your question or asking about a specific city."
        )
        if DEBUG_APPEND_PROMPT:
            planner_raw = state.get("planner_raw")
            if planner_raw:
                answer += f"\n\n----- planner response -----\n{planner_raw}"
        return {"answer": answer}

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
        grounding_policy=GROUNDING_POLICY,
        vector_section=vector_section,
        graph_section=graph_section,
        question=state["question"],
    )
    prompt = external_section + prompt
    answer = await llm.generate(prompt)
    return {"answer": _append_debug_footer(answer, state, prompt)}


async def format_node(state: GraphState) -> GraphState:
    """Prepare final response. Currently a pass-through for future post-processing."""
    return {}
