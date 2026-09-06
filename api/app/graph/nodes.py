import json
import logging
import re

from langgraph.config import get_stream_writer
from pydantic import ValidationError

from app.graph.state import GraphState
from app.graph.history_context import (
    decide_include_history,
    question_block,
    sanitize_history,
)
from app.models.planner import parse_planner_output, planner_state_update
from app.services import graph_store
from app.services import llm
from app.services import vector_store
from app.config import settings
from app.services.guard import REJECT_MESSAGE, get_guard_client

logger = logging.getLogger(__name__)

# TODO: remove before prod — appends LLM prompts to the answer for debugging
DEBUG_APPEND_PROMPT = False

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PLANNER_PROMPT = """You are a travel assistant planner. Analyze the user's question and return ONLY valid JSON (no markdown fences or extra commentary).

Schema:
{{
  "primary_intent": "chitchat|factoid|recommendation|planning|comparison|logistics|meta",
  "in_scope": true,
  "needs_clarification": false,
  "clarification": {{
    "prompt": "",
    "suggestions": []
  }},
  "intents": [
    {{ "intent": "planning", "cities": ["Antalya"] }}
  ],
  "prompt_language": "ISO 639-1 code of the user's message (e.g. tr, en, de)",
  "plan": {{
    "summary": "One short active sentence in the user's language describing what you will do next",
    "steps": [
      {{ "id": "s1", "label": "descriptive action in the user's language", "kind": "vector|graph|museums|artifacts|external|synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [
    {{ "q": "retrieval-optimized phrase 1", "city": "Antalya" }},
    {{ "q": "optional global phrase", "city": "ANY" }}
  ],
  "deep_search": false,
  "search_museums": false,
  "search_artifacts": false,
  "museum_search_queries": [],
  "artifact_search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": false,
  "graph_queries": []
}}

Rules:
- prompt_language: detect the language of the user's message; use ISO 639-1 (tr, en, de, fr, ...).
- summary and step labels: write in the user's language (prompt_language). Keep summary under 15 words.
- in_scope: true when the user's request (resolving references via Recent conversation if present) is about travel, tourism, cities, places, destinations, attractions, museums, itineraries, lodging, or trip flights. Questions about a specific city or place count as in-scope.
- in_scope: false for weather-only forecasts, coding, medical advice, crime, unrelated trivia, gibberish, or anything not about travel/places — even if history mentioned a city. Then set primary_intent to "meta", intents to [], needs_clarification to false, clarification to {{ "prompt": "", "suggestions": [] }}, all query lists to [], and needs_graph/deep_search/search_museums/search_artifacts/is_hotel_search/is_flight_search to false. Do NOT classify off-topic refusals as chitchat.
- needs_clarification: true when in_scope is true, a city/place is clear, but the ask is too broad to retrieve well (e.g. "I want to visit Antalya", "Antalya'yı gezmek istiyorum", "tell me about Istanbul", bare city name). Then YOU write clarification.prompt (short question in the user's language) and 3-5 clarification.suggestions — each suggestion must be a full ready-to-send follow-up question including the city name. Clear all search/query lists and search flags. Do NOT retrieve yet.
- needs_clarification: false when the question is already specific (museums, N-day plan, hotels, compare, logistics, named POI, etc.). Then set clarification to {{ "prompt": "", "suggestions": [] }}.
- primary_intent: the single main goal of the question. Use chitchat only for greetings or questions about this assistant's capabilities (those stay in_scope true).
- intents: zero or more task slots. Order does not matter. Each item is {{ "intent": "...", "cities": [...] }}.
  Allowed intents[].intent values: factoid, recommendation, planning, comparison, logistics.
  Do NOT put chitchat or meta inside intents[]; those are primary_intent only (use intents: []).
  cities: cities relevant to that specific intent (empty list if none). Do not invent cities that are not in the question or Recent conversation.
  If the user mixes goals (e.g. plan + compare), emit multiple intents entries with cities scoped per intent.
  When primary_intent is a task intent (not chitchat/meta), in_scope is true, and needs_clarification is false, include a matching entry in intents[].
- steps: generate 2-5 steps tailored to the question. Each step needs a unique id (s1, s2, ...), a descriptive label, and a kind. The last step must always be kind "synthesize". Use kind "vector" for Wikipedia/guide retrieval, "museums" / "artifacts" when deep POI search is on, "graph" when needs_graph is true, "external" when hotel or flight search links are prepared. All steps start with status "idle".
- For chitchat, meta, in_scope false, or needs_clarification: use a single synthesize step and set search_queries to [], museum_search_queries to [], artifact_search_queries to [], graph_queries to [], needs_graph/deep_search/search_museums/search_artifacts/is_hotel_search/is_flight_search to false.
- search_queries: 1-3 objects for Wikipedia/guide vector search (empty for chitchat/meta/out-of-scope/clarification). Each item is {{ "q": "English phrase", "city": "CityName|ANY" }}.
  q: short English phrase for embedding against guide chunks (no hotel/flight URLs; separate from museum/artifact queries).
  city: pin results to that city metadata when known; use "ANY" when the phrase is country-wide, comparative across unknown places, or city should not be filtered. City is optional — omit or "ANY" means no city filter.
  For multi-city questions, prefer one query object per city (same or different q) rather than a single unfiltered query.
  Retrieval query rules:
  - Generate queries for information that must be retrieved, not for the final output format.
  - Match queries to vocabulary and themes likely in the indexed corpus (Wikipedia city/guide sections and related POI pages).
  - For itinerary / "N-day route" requests, trip duration is a synthesis constraint — do not normally put "2 day", "5 day", or "itinerary" in q.
  - Avoid generic web-search phrases such as "top attractions", "things to do", "best places", and "N-day itinerary".
  - Prefer factual themes: landmarks, main sights, districts, museums, historical sites, architecture, parks, transport, geography, cuisine when relevant.
  - Use semantic phrases aligned with those themes, not only exact section titles; prefer 2-3 complementary themes over near-duplicate queries.
  - Example: "İstanbul'da 2 günlük rota" → q like "historic peninsula landmarks", "museums galleries", "public transport" — NOT "2 day itinerary".

Deep POI search (museums + historical sites):
- deep_search: true when museum or historical-site detail would materially help (sights, museums, ruins, castles, temples, UNESCO sites, "what to see", "tarihi yerler", "müzeler").
- Also turn deep_search on for open-ended city sightseeing / places-to-go questions even when museums or ruins are not named explicitly, e.g. "where to go", "what to see", "things to do", "places to visit", "aileyle gidilecek yerler", "gezilecek yerler", "nereler gezilir", family/kid recommendations for a city, or a city itinerary/day plan.
- search_museums: true for museums, galleries, collections; also true for broad sightseeing questions above (museum POIs often help).
- search_artifacts: true for historical sites, ruins, monuments, archaeological places, castles, temples; also true for broad sightseeing questions above unless the user clearly wants only museums (or only historical sites).
- For broad sightseeing / family places / city itinerary: prefer search_museums=true AND search_artifacts=true.
- deep_search must be true iff search_museums or search_artifacts is true.
- museum_search_queries: when search_museums is true, 1-3 {{ "q", "city" }} objects for the museums collection. When false, [].
- artifact_search_queries: when search_artifacts is true, 1-3 {{ "q", "city" }} objects for the historical-sites collection. When false, [].
- Same city rules as search_queries (CityName or ANY).
- Keep deep_search false for: pure hotel/flight, food-only, beach-weather-only, transport-only logistics, chitchat/meta, or asks unrelated to POIs. Then both query lists [].

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

Neo4j graph (see neo4j.md — constraints / candidates, not prose):
- needs_graph: true only when structured relationships help (day trips with time, city-to-city transport, airports serving a city, place lists by type in a city).
- graph_queries: 0-3 objects {{ "template": "...", "params": {{...}} }}. When needs_graph is false, [].
- Allowed templates ONLY:
  - places_in_city — params: city_slug (required), place_type (optional: museum|historical_site|attraction|...), limit
  - day_trips_from_city — params: city_slug, max_one_way_minutes (optional int), place_type (optional), limit
  - connection_between_cities — params: city_a, city_b
  - airports_serving_city — params: city_slug
- city_slug may be a slug or display name (Antalya / antalya). Do NOT invent Cypher.
- Prefer graph for DAY_TRIP / CONNECTED_BY / airport questions; prefer vector/deep alone for open-ended "what to see" prose.

Example — question: "Antalya'yı gezmek istiyorum"
{{
  "primary_intent": "recommendation",
  "in_scope": true,
  "needs_clarification": true,
  "clarification": {{
    "prompt": "Antalya hakkında ne bilmek istersiniz?",
    "suggestions": [
      "Antalya'nın müzelerini sırala",
      "Antalya'da tarihi yerler neler?",
      "Antalya'da 2 günlük rota öner",
      "Antalya'da aileyle gidilecek yerler"
    ]
  }},
  "intents": [
    {{ "intent": "recommendation", "cities": ["Antalya"] }}
  ],
  "prompt_language": "tr",
  "plan": {{
    "summary": "Antalya için net seçenekler sunuyorum.",
    "steps": [
      {{ "id": "s1", "label": "Seçenekleri sun", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [],
  "deep_search": false,
  "search_museums": false,
  "search_artifacts": false,
  "museum_search_queries": [],
  "artifact_search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": false,
  "graph_queries": []
}}

Example — question: "Where can I go with my family in Antalya?"
{{
  "primary_intent": "recommendation",
  "in_scope": true,
  "intents": [
    {{ "intent": "recommendation", "cities": ["Antalya"] }}
  ],
  "prompt_language": "en",
  "plan": {{
    "summary": "Searching for family-friendly places in Antalya.",
    "steps": [
      {{ "id": "s1", "label": "Search family-friendly spots in Antalya", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Search museums for families", "kind": "museums", "status": "idle" }},
      {{ "id": "s3", "label": "Search historical sites nearby", "kind": "artifacts", "status": "idle" }},
      {{ "id": "s4", "label": "Compile and present recommendations", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [
    {{ "q": "family parks recreation", "city": "Antalya" }},
    {{ "q": "main sights landmarks", "city": "Antalya" }}
  ],
  "deep_search": true,
  "search_museums": true,
  "search_artifacts": true,
  "museum_search_queries": [
    {{ "q": "museums galleries", "city": "Antalya" }}
  ],
  "artifact_search_queries": [
    {{ "q": "historical sites ruins", "city": "Antalya" }}
  ],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": false,
  "graph_queries": []
}}

Example — question: "Antalya'da 5 gün planla, Bodrum ile karşılaştır"
{{
  "primary_intent": "planning",
  "in_scope": true,
  "intents": [
    {{ "intent": "planning", "cities": ["Antalya"] }},
    {{ "intent": "comparison", "cities": ["Antalya", "Bodrum"] }}
  ],
  "prompt_language": "tr",
  "plan": {{
    "summary": "Antalya planı hazırlayıp Bodrum ile karşılaştırıyorum.",
    "steps": [
      {{ "id": "s1", "label": "Antalya ve Bodrum bilgilerini araştır", "kind": "vector", "status": "idle" }},
      {{ "id": "s2", "label": "Müze ve tarihi yerleri tara", "kind": "museums", "status": "idle" }},
      {{ "id": "s3", "label": "Plan ve karşılaştırmayı sentezle", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [
    {{ "q": "main sights landmarks museums", "city": "Antalya" }},
    {{ "q": "main sights landmarks", "city": "Bodrum" }},
    {{ "q": "transport geography", "city": "Antalya" }}
  ],
  "deep_search": true,
  "search_museums": true,
  "search_artifacts": true,
  "museum_search_queries": [
    {{ "q": "museums galleries", "city": "Antalya" }},
    {{ "q": "museums galleries", "city": "Bodrum" }}
  ],
  "artifact_search_queries": [
    {{ "q": "historical sites ruins", "city": "Antalya" }},
    {{ "q": "historical sites castle", "city": "Bodrum" }}
  ],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": true,
  "graph_queries": [
    {{ "template": "connection_between_cities", "params": {{ "city_a": "Antalya", "city_b": "Bodrum" }} }}
  ]
}}

Example — question: "İstanbul'da hangi müzeleri gezmeliyim?"
{{
  "primary_intent": "recommendation",
  "in_scope": true,
  "intents": [
    {{ "intent": "recommendation", "cities": ["İstanbul"] }}
  ],
  "prompt_language": "tr",
  "plan": {{
    "summary": "İstanbul müzelerini araştırıyorum.",
    "steps": [
      {{ "id": "s1", "label": "İstanbul müzelerini tara", "kind": "museums", "status": "idle" }},
      {{ "id": "s2", "label": "Rehber özetini getir", "kind": "vector", "status": "idle" }},
      {{ "id": "s3", "label": "Müze önerilerini sentezle", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [
    {{ "q": "museums galleries", "city": "Istanbul" }},
    {{ "q": "architecture cityscape", "city": "Istanbul" }}
  ],
  "deep_search": true,
  "search_museums": true,
  "search_artifacts": false,
  "museum_search_queries": [
    {{ "q": "archaeology museums", "city": "Istanbul" }},
    {{ "q": "art museums galleries", "city": "Istanbul" }}
  ],
  "artifact_search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": true,
  "graph_queries": [
    {{ "template": "places_in_city", "params": {{ "city_slug": "Istanbul", "place_type": "museum" }} }}
  ]
}}

Example — question: "İstanbul'da 5 günlük rota, otel ve uçuş öner"
{{
  "primary_intent": "planning",
  "in_scope": true,
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
  "search_queries": [
    {{ "q": "historic peninsula landmarks main sights", "city": "Istanbul" }},
    {{ "q": "museums galleries", "city": "Istanbul" }},
    {{ "q": "public transport districts", "city": "Istanbul" }}
  ],
  "deep_search": true,
  "search_museums": true,
  "search_artifacts": true,
  "museum_search_queries": [
    {{ "q": "museums galleries", "city": "Istanbul" }}
  ],
  "artifact_search_queries": [
    {{ "q": "historical sites monuments", "city": "Istanbul" }}
  ],
  "is_hotel_search": true,
  "hotel_search_query": "https://www.booking.com/searchresults.html?ss=İstanbul",
  "is_flight_search": true,
  "flight_search_query": "https://www.kayak.com/flights/IST-IST?sort=bestflight_a",
  "needs_graph": false,
  "graph_queries": []
}}

Example — question: "Hello, what can you do?"
{{
  "primary_intent": "chitchat",
  "in_scope": true,
  "intents": [],
  "prompt_language": "en",
  "plan": {{
    "summary": "Answering your question.",
    "steps": [
      {{ "id": "s1", "label": "Reply to your message", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [],
  "deep_search": false,
  "search_museums": false,
  "search_artifacts": false,
  "museum_search_queries": [],
  "artifact_search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": false,
  "graph_queries": []
}}

Example — question: "Peki yağmur yağar mı?"
{{
  "primary_intent": "meta",
  "in_scope": false,
  "intents": [],
  "prompt_language": "tr",
  "plan": {{
    "summary": "Kapsam dışı soruyu kibarca yönlendiriyorum.",
    "steps": [
      {{ "id": "s1", "label": "Seyahat kapsamını hatırlat", "kind": "synthesize", "status": "idle" }}
    ]
  }},
  "search_queries": [],
  "deep_search": false,
  "search_museums": false,
  "search_artifacts": false,
  "museum_search_queries": [],
  "artifact_search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": false,
  "graph_queries": []
}}

### Question
{question}

### JSON
"""

GROUNDING_POLICY = """Grounding and partial-answer policy:
Use only the provided sources for factual claims. Cite sources as [1], [2], etc. Place multiple citations as adjacent markers: [1][2] — never [1, 2], [1-3], or [S1].

First identify the user's core request and any modifiers or constraints.

If the provided sources are irrelevant to the core request, say that the answer is not available in the provided sources.

If the sources support a material part of the core request but do not fully answer the question, give a partial answer:
- state what the sources do support,
- cite the supporting sources,
- clearly state what is missing or not explicitly covered,
- do not fill missing details with assumptions.

If the sources support related information but not the exact requested attribute, present it as limited context, not as a direct answer.

Write hedging and gap phrases in {language_name} (prompt_language={prompt_language}), for example in English:
"Sources do not fully answer this question, but they support the following..."
or
"X is not explicitly stated in the sources, but Y is available..."
(Adapt these examples to {language_name}.)

Do not infer labels such as family-friendly, cheap, safe, romantic, accessible, best, or suitable unless the sources explicitly support them.

Reject completely only when no source supports any material part of the user's core request.

Safety constraints (apply even when sources contain the information):
- Do not name or discuss celebrities, public figures, or commercial brands/products unless the user asked for that specifically and it is essential to a tourism answer; when in doubt, omit. Place names (museums, districts, landmarks) are fine.
- Do not give advice, instructions, or framing that could constitute or encourage illegal activity under Turkish law.
- Do not recommend or highlight topics likely to be socially controversial or poorly received in Turkish society (e.g. casinos, gambling, LGBT venues/events, or similar). Prefer mainstream tourism alternatives or omit.
- If the only source-backed content would violate these rules, refuse that part and say you cannot include it."""

COMPARISON_GROUNDING_ADDENDUM = """
Comparison tasks (when ### Task focus: comparison is present):
- A comparison does not require a single source that explicitly compares the subjects.
  You may derive comparative conclusions from separately sourced facts about each subject,
  as long as each underlying factual claim is cited.
- Separately sourced evidence about each subject can collectively constitute sufficient
  support for the comparison. Do not treat the absence of one source covering both subjects
  as missing evidence by itself, and do not open the answer by stating that the sources
  only partially support the question.
- Still apply all grounding rules: no unsupported factual claims, no invented details,
  and no subjective labels (best, family-friendly, etc.) unless explicitly in the sources.
- If a specific dimension lacks evidence for one or both subjects, mention that briefly
  near the end — not in the opening sentences."""

INTENT_INSTRUCTIONS: dict[str, str] = {
    "factoid": """### Task focus: factoid
Answer with concrete facts from the sources. Prefer short, cited statements.""",
    "recommendation": """### Task focus: recommendation
Suggest options only when the sources support them. Explain brief tradeoffs with citations.""",
    "planning": """### Task focus: planning
Structure the answer as a sequenced plan (e.g. day-by-day). Note gaps instead of inventing times or logistics.""",
    "comparison": """### Task focus: comparison

Produce a comparative synthesis, not two separate summaries.

Open with 2–4 sentences that directly state the most important similarities, differences,
and tradeoffs between the subjects. Each sentence should contrast or relate the subjects;
do not merely describe one subject at a time.

Do not open with meta-commentary such as:
"The provided sources..."
"The sources indicate..."
"Based on the retrieved information..."

A comparison does not require a source that explicitly compares the subjects. You may derive
comparative conclusions from separately sourced facts when the underlying factual claims
are supported and cited.

Structure the body around shared comparison dimensions supported by the evidence
(e.g. museums, historical sites, cityscape, tourism, transport).

Within each dimension, discuss both subjects together and explicitly contrast them.
Prefer formulations such as:
- "X..., whereas Y..."
- "Both..., but..."
- "Compared with X, Y..."

Do not default to separate inventories of facts for each subject (e.g. "City A has…" then
"City B has…" under the same heading without contrast).

Use citations for factual claims throughout.

If evidence is genuinely insufficient for an important dimension, mention that limitation
briefly near the end rather than leading with it.

A markdown comparison table is optional. Use one only when the sources support enough
concrete, comparable facts across the subjects (at least two meaningful rows with real
values) and a table improves clarity. Otherwise compare in prose.

When you do use a table:
- Do not put it at the very start of the answer.
- The table body must have at most 5 data rows (header row does not count).
- Keep columns few and readable; leave cells empty or say "not in sources" rather than inventing values.""",
    "logistics": """### Task focus: logistics
Focus on how to get around, timing, transport, and practical constraints grounded in the sources.""",
}

SYNTHESIZE_PROMPT = """You are a travel assistant. Answer the user's question using the provided sources.
Reply entirely in {language_name} (ISO 639-1: {prompt_language}). Do not switch languages except for proper nouns.

Cite sources as [1], [2], etc. Place multiple citations as adjacent markers: [1][2] — never [1, 2], [1-3], or [S1]. Digits only inside each pair of brackets. Each source line is tagged with its kind (museum, historical, guide, or graph).

{grounding_policy}

{intent_section}{sources_section}### Question
{question}

### Answer
"""

SYNTHESIZE_SOURCES_SECTION = """### Sources
{sources}

"""

SYNTHESIZE_EXTERNAL_SECTION = """### External booking links
Include these links in your answer when relevant (use markdown links with descriptive labels in {language_name}):
{external_links}

"""

CHITCHAT_PROMPT = """You are a friendly travel assistant. The user is making casual conversation or asking about your capabilities.
Reply naturally and entirely in {language_name} (ISO 639-1: {prompt_language}). Keep it brief and warm.

### Message
{question}

### Reply
"""

# ISO 639-1 → English language name for LLM instructions
_LANGUAGE_NAMES: dict[str, str] = {
    "tr": "Turkish",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "el": "Greek",
    "pl": "Polish",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "hu": "Hungarian",
    "cs": "Czech",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "he": "Hebrew",
    "hi": "Hindi",
}


def _prompt_language_fields(state: GraphState) -> dict[str, str]:
    raw = state.get("prompt_language") or "en"
    code = str(raw).strip().lower().replace("_", "-").split("-", 1)[0] or "en"
    if len(code) > 3:
        code = code[:2]
    name = _LANGUAGE_NAMES.get(code, code)
    return {"prompt_language": code, "language_name": name}


def _prompt_question(state: GraphState) -> str:
    """Question for LLM prompts; history only when intake set include_history."""
    return question_block(
        state.get("question") or "",
        state.get("history") or [],
        include_history=bool(state.get("include_history")),
    )

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


async def _collect_query_chunks(
    queries: list,
    *,
    collection: str | None = None,
    top_k: int = 5,
    limit: int = 10,
) -> list[dict]:
    all_chunks: list[dict] = []
    seen: set[tuple] = set()
    for item in queries:
        if isinstance(item, str):
            q, city = item.strip(), None
        elif isinstance(item, dict):
            q = (item.get("q") or item.get("query") or "").strip()
            city = item.get("city")
            if isinstance(city, str) and city.strip().upper() == "ANY":
                city = None
            elif isinstance(city, str):
                city = city.strip() or None
            else:
                city = None
        else:
            q = getattr(item, "q", "") or ""
            city = getattr(item, "city", None)
            q = q.strip() if isinstance(q, str) else ""
        if not q:
            continue
        try:
            results = await vector_store.search(
                q, top_k=top_k, collection=collection, city=city
            )
        except Exception as e:
            logger.warning(
                "Vector search failed for %r city=%r (%s): %s",
                q,
                city,
                collection,
                e,
            )
            continue
        for chunk in results:
            section = (chunk.get("section") or "").strip()
            leaf = section.split("/")[-1].strip().lower()
            key = (
                (chunk.get("source_url") or "").strip().lower(),
                leaf,
            )
            if key not in seen:
                seen.add(key)
                all_chunks.append(chunk)
    all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
    return all_chunks[:limit]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def intake_node(state: GraphState) -> GraphState:
    question = (state.get("question") or "").strip()
    if not question:
        return {"error": "Empty question"}
    max_chars = settings.max_question_chars
    if max_chars > 0 and len(question) > max_chars:
        logger.info(
            "Truncating question from %d to %d chars",
            len(question),
            max_chars,
        )
        question = question[:max_chars]

    history = sanitize_history(state.get("history"))
    include_history = decide_include_history(question, history)
    if include_history:
        logger.info("include_history=true (%d turn(s))", len(history))

    return {
        "question": question,
        "history": history,
        "include_history": include_history,
    }


async def safety_guard_node(state: GraphState) -> GraphState:
    """First model node: Qwen3Guard. Only Safety: Safe continues."""
    if state.get("error"):
        return {}

    question = state.get("question") or ""
    try:
        result = await get_guard_client().check_prompt(question)
    except Exception as e:
        logger.warning("Safety guard failed: %s", e)
        return {
            "error": REJECT_MESSAGE,
            "guard_label": None,
            "guard_raw": str(e),
        }

    logger.info(
        "Safety guard label=%s categories=%s allowed=%s",
        result.label,
        result.categories,
        result.allowed,
    )
    if not result.allowed:
        return {
            "error": REJECT_MESSAGE,
            "guard_label": result.label,
            "guard_raw": result.raw,
        }
    return {
        "guard_label": result.label or "Safe",
        "guard_raw": result.raw,
    }


async def planner_json_node(state: GraphState) -> GraphState:
    if state.get("error"):
        return {}

    prompt = PLANNER_PROMPT.format(question=_prompt_question(state))
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


async def deep_retrieval_node(state: GraphState) -> GraphState:
    """Search museum / historical-site collections using planner query lists."""
    museum_chunks: list[dict] = []
    artifact_chunks: list[dict] = []

    if state.get("search_museums"):
        museum_chunks = await _collect_query_chunks(
            state.get("museum_search_queries") or [],
            collection=settings.qdrant_collection_museums,
        )

    if state.get("search_artifacts"):
        artifact_chunks = await _collect_query_chunks(
            state.get("artifact_search_queries") or [],
            collection=settings.qdrant_collection_historical,
        )

    return {
        "museum_chunks": museum_chunks,
        "artifact_chunks": artifact_chunks,
    }


async def vector_retrieval_node(state: GraphState) -> GraphState:
    queries = state.get("search_queries") or []
    if not queries:
        return {"vector_chunks": [], "sources": []}

    all_chunks = await _collect_query_chunks(queries)
    return {
        "vector_chunks": all_chunks,
        "sources": all_chunks,
    }


async def graph_retrieval_node(state: GraphState) -> GraphState:
    if not state.get("needs_graph"):
        return {"graph_contexts": []}

    queries = state.get("graph_queries") or []
    if not queries:
        return {"graph_contexts": []}

    try:
        contexts = await graph_store.fetch_contexts_for_queries(queries)
    except Exception as e:
        logger.warning("Graph retrieval failed: %s", e)
        contexts = []

    return {"graph_contexts": contexts}


async def _stream_answer(prompt: str) -> str:
    """Generate answer while emitting token deltas via LangGraph custom stream."""
    parts: list[str] = []
    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = None

    async for delta in llm.stream_generate(prompt):
        parts.append(delta)
        if writer is not None:
            writer({"type": "token", "delta": delta})
    return "".join(parts)


async def clarify_node(state: GraphState) -> GraphState:
    """Broad city ask → show LLM-authored follow-up chips (no retrieval)."""
    clar = state.get("clarification") or {}
    prompt = ""
    suggestions: list[str] = []
    if isinstance(clar, dict):
        raw_prompt = clar.get("prompt")
        if isinstance(raw_prompt, str):
            prompt = raw_prompt.strip()
        raw_sugs = clar.get("suggestions")
        if isinstance(raw_sugs, list):
            suggestions = [s.strip() for s in raw_sugs if isinstance(s, str) and s.strip()]
    if not prompt:
        prompt = "What would you like to know?"
    debug = (
        f"clarify needs_clarification={state.get('needs_clarification')}\n"
        f"suggestions={suggestions!r}\n\n"
        f"### Question\n{_prompt_question(state)}"
    )
    return {
        "answer": _append_debug_footer(prompt, state, debug),
        "suggestions": suggestions,
        "sources": [],
    }


async def chitchat_node(state: GraphState) -> GraphState:
    lang = _prompt_language_fields(state)
    prompt = CHITCHAT_PROMPT.format(question=_prompt_question(state), **lang)
    answer = await _stream_answer(prompt)
    return {"answer": _append_debug_footer(answer, state, prompt), "sources": []}


_OUT_OF_SCOPE_MESSAGES: dict[str, str] = {
    "en": (
        "I'm a travel assistant for cities, places, and trip planning. "
        "I can help with destinations, itineraries, museums, sights, lodging, or getting around — "
        "ask me about a place or trip."
    ),
    "tr": (
        "Ben şehirler, gezilecek yerler ve seyahat planı konusunda yardımcı olan bir asistanım. "
        "Destinasyonlar, rotalar, müzeler, gezilecek yerler, konaklama veya ulaşım hakkında "
        "sorabilirsin — bir yer veya gezi sorusuyla devam edebilirsin."
    ),
    "de": (
        "Ich bin ein Reiseassistent für Städte, Orte und Reiseplanung. "
        "Fragen Sie mich nach Zielen, Routen, Museen, Sehenswürdigkeiten, Unterkunft oder Anreise."
    ),
}


async def out_of_scope_node(state: GraphState) -> GraphState:
    """Polite refusal that reminds the user of the travel/cities scope (not chitchat)."""
    lang = _prompt_language_fields(state)
    code = lang["prompt_language"]
    answer = _OUT_OF_SCOPE_MESSAGES.get(code) or _OUT_OF_SCOPE_MESSAGES["en"]
    debug_prompt = (
        f"out_of_scope in_scope={state.get('in_scope')}\n\n"
        f"### Question\n{_prompt_question(state)}"
    )
    return {
        "answer": _append_debug_footer(answer, state, debug_prompt),
        "sources": [],
    }


def _build_external_links_section(state: GraphState) -> str:
    links: list[str] = []
    if state.get("is_hotel_search") and state.get("hotel_search_query"):
        links.append(f"Hotel: {state['hotel_search_query']}")
    if state.get("is_flight_search") and state.get("flight_search_query"):
        links.append(f"Flights: {state['flight_search_query']}")
    if not links:
        return ""
    lang = _prompt_language_fields(state)
    return SYNTHESIZE_EXTERNAL_SECTION.format(
        external_links="\n".join(links),
        **lang,
    )


def _has_comparison_intent(state: GraphState) -> bool:
    if state.get("primary_intent") == "comparison":
        return True
    for slot in state.get("intents") or []:
        if isinstance(slot, dict) and slot.get("intent") == "comparison":
            return True
    return False


def _build_grounding_policy(state: GraphState, lang: dict[str, str]) -> str:
    policy = GROUNDING_POLICY.format(**lang)
    if _has_comparison_intent(state):
        policy += COMPARISON_GROUNDING_ADDENDUM
    return policy


def _build_intent_section(state: GraphState) -> str:
    """Compose task-focus blocks from intents[] (primary first)."""
    slots = state.get("intents") or []
    names: list[str] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        name = slot.get("intent")
        if isinstance(name, str) and name in INTENT_INSTRUCTIONS and name not in names:
            names.append(name)

    primary = state.get("primary_intent") or ""
    if primary in INTENT_INSTRUCTIONS and primary not in names:
        names.insert(0, primary)

    ordered: list[str] = []
    if primary in names:
        ordered.append(primary)
    ordered.extend(name for name in names if name not in ordered)

    if not ordered:
        return ""

    bullet_lines: list[str] = []
    for name in ordered:
        cities: list[str] = []
        for slot in slots:
            if isinstance(slot, dict) and slot.get("intent") == name:
                raw_cities = slot.get("cities") or []
                if isinstance(raw_cities, list):
                    cities = [c for c in raw_cities if isinstance(c, str) and c.strip()]
                break
        if cities:
            bullet_lines.append(f"- {name} ({', '.join(cities)})")
        else:
            bullet_lines.append(f"- {name}")

    blocks = [
        "### Active intents\n" + "\n".join(bullet_lines),
        *(INTENT_INSTRUCTIONS[name] for name in ordered),
    ]
    return "\n\n".join(blocks) + "\n\n"


def _chunk_label(chunk: dict) -> str:
    title = chunk.get("title") or ""
    name = chunk.get("name") or title or ""
    city = chunk.get("city_name") or ""
    section = chunk.get("section") or ""
    if name and city and name.casefold() != city.casefold():
        return f"{name} / {city}"
    if name:
        return name
    if city and section:
        return f"{city} / {section}"
    return city or section or "source"


def _source_dedupe_key(source: dict) -> str:
    """Same page URL + same section → one citation; different sections stay distinct."""
    url = (source.get("source_url") or "").strip().rstrip("/")
    section = (source.get("section") or "").strip().casefold()
    if url:
        return f"{url}#{section}"
    city = (source.get("city_name") or "").strip().casefold()
    name = (source.get("name") or "").strip().casefold()
    return f"{city}:{section}:{name}"


def _merge_deduped_source(
    existing: tuple[str, str, dict],
    label: str,
    text: str,
    source: dict,
) -> tuple[str, str, dict]:
    prev_label, prev_text, prev_source = existing
    new_text = (text or "").strip()
    merged_text = prev_text
    if new_text and new_text not in prev_text:
        merged_text = f"{prev_text}\n\n{new_text}" if prev_text else new_text

    merged = dict(prev_source)
    try:
        prev_score = float(prev_source.get("score") or 0)
        new_score = float(source.get("score") or 0)
    except (TypeError, ValueError):
        prev_score, new_score = 0.0, 0.0
    if new_score > prev_score:
        merged["score"] = new_score
        for key in ("name", "title", "city_name", "section", "source_url", "source", "poi_type"):
            if source.get(key):
                merged[key] = source[key]
    merged["text"] = merged_text
    return prev_label or label, merged_text, merged


def _build_unified_sources(
    museum_chunks: list[dict],
    artifact_chunks: list[dict],
    vector_chunks: list[dict],
    graph_contexts: list[dict],
) -> tuple[str, list[dict]]:
    """Dedupe by (url, section), then number as [1]…[n].

    Order: museum → historical → guide → graph.
    """
    # key → (label, text, source); insertion order preserved.
    merged: dict[str, tuple[str, str, dict]] = {}

    def add(label: str, text: str, source: dict) -> None:
        key = _source_dedupe_key(source)
        if key in merged:
            merged[key] = _merge_deduped_source(merged[key], label, text, source)
        else:
            payload = dict(source)
            payload["text"] = text
            merged[key] = (label, text, payload)

    for chunk in museum_chunks:
        add(f"museum · {_chunk_label(chunk)}", chunk.get("text") or "", chunk)

    for chunk in artifact_chunks:
        add(f"historical · {_chunk_label(chunk)}", chunk.get("text") or "", chunk)

    for chunk in vector_chunks:
        city = chunk.get("city_name") or ""
        section = chunk.get("section") or ""
        place = f"{city} / {section}".strip(" /") or _chunk_label(chunk)
        add(f"guide · {place}", chunk.get("text") or "", chunk)

    for ctx in graph_contexts:
        label = (ctx.get("name") or ctx.get("city") or "").strip()
        url = (ctx.get("source_url") or "").strip()
        source_kind = (ctx.get("source") or "").strip()
        if not source_kind:
            if "wikipedia" in url.lower():
                source_kind = "wikipedia"
        graph_as_source = {
            "text": ctx.get("text") or "",
            "city_name": label,
            "section": "",
            "source_url": url,
            "source": source_kind,
            "name": label,
            "poi_type": "",
            "score": 0.0,
        }
        display = label or "graph"
        add(f"graph · {display}", ctx.get("text") or "", graph_as_source)

    if not merged:
        return "", []

    lines: list[str] = []
    sources: list[dict] = []
    for i, (label, text, source) in enumerate(merged.values(), start=1):
        lines.append(f"[{i}] ({label}):\n{text}")
        sources.append(source)

    section = SYNTHESIZE_SOURCES_SECTION.format(sources="\n\n".join(lines))
    return section, sources


_CITATION_RE = re.compile(r"(?<![\w/])\[(\d+)\]")


def _filter_and_renumber_citations(
    answer: str,
    sources: list[dict],
) -> tuple[str, list[dict]]:
    """Keep only sources cited as [n] in the answer; renumber densely to [1]…[k].

    Must run before any debug footer is appended (footer may contain [n]).
    """
    if not sources:
        return answer, []

    used = sorted(
        {
            int(m.group(1))
            for m in _CITATION_RE.finditer(answer or "")
            if 1 <= int(m.group(1)) <= len(sources)
        }
    )
    if not used:
        return answer, []

    old_to_new = {old: new for new, old in enumerate(used, start=1)}
    cited = [sources[old - 1] for old in used]

    def repl(match: re.Match[str]) -> str:
        old = int(match.group(1))
        new = old_to_new.get(old)
        return f"[{new}]" if new is not None else match.group(0)

    return _CITATION_RE.sub(repl, answer), cited


async def synthesize_node(state: GraphState) -> GraphState:
    chunks = state.get("vector_chunks") or []
    museum_chunks = state.get("museum_chunks") or []
    artifact_chunks = state.get("artifact_chunks") or []
    contexts = state.get("graph_contexts") or []
    external_section = _build_external_links_section(state)
    has_external_only = (
        bool(external_section)
        and not chunks
        and not museum_chunks
        and not artifact_chunks
        and not contexts
    )

    if (
        not chunks
        and not museum_chunks
        and not artifact_chunks
        and not contexts
        and not has_external_only
    ):
        answer = (
            "I couldn't find relevant information in my sources. "
            "Try rephrasing your question or asking about a specific city."
        )
        if DEBUG_APPEND_PROMPT:
            debug_prompt = (
                f"include_history={bool(state.get('include_history'))}\n"
                f"history_turns={len(state.get('history') or [])}\n\n"
                f"### Question\n{_prompt_question(state)}"
            )
            answer = _append_debug_footer(answer, state, debug_prompt)
        return {"answer": answer}

    sources_section, sources = _build_unified_sources(
        museum_chunks, artifact_chunks, chunks, contexts
    )
    intent_section = _build_intent_section(state)
    lang = _prompt_language_fields(state)

    prompt = SYNTHESIZE_PROMPT.format(
        grounding_policy=_build_grounding_policy(state, lang),
        intent_section=intent_section,
        sources_section=sources_section,
        question=_prompt_question(state),
        **lang,
    )
    prompt = external_section + prompt
    answer = await _stream_answer(prompt)
    answer, sources = _filter_and_renumber_citations(answer, sources)

    return {
        "answer": _append_debug_footer(answer, state, prompt),
        "sources": sources,
    }


async def format_node(state: GraphState) -> GraphState:
    """Prepare final response. Currently a pass-through for future post-processing."""
    return {}
