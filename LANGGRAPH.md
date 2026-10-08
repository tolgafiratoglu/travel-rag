# Travel RAG — LangGraph Design

This file documents the **currently running** LangGraph pipeline. Code: `api/app/graph/` (`state.py`, `nodes.py`, `edges.py`, `builder.py`).

Neo4j schema + templates: **[`neo4j.md`](./neo4j.md)** (graph = candidates/constraints; Qdrant = narrative).

---

## Graph (current)

```
START
  → intake
  → safety_guard
       ├─ (blocked / error) → format → END
       └─ planner_json
            → retrieval_router
                 ├─ chitchat | meta → chitchat → format → END
                 ├─ needs_graph → graph_retrieval → (deep?) → vector → synthesize → format → END
                 ├─ deep_search → deep_retrieval → vector → synthesize → format → END
                 └─ else → vector_retrieval → synthesize → format → END
```

| Node | LLM? | Role |
|------|------|------|
| `intake` | No | Normalizes the question |
| `safety_guard` | Guard model (Ollama) | Prompt injection / unsafe input |
| `planner_json` | **Yes (#1)** | Intent, plan, search + optional `graph_queries` |
| `chitchat` | **Yes** | Short answer without retrieval |
| `graph_retrieval` | No | Neo4j allowlist template Cypher (`needs_graph`) |
| `deep_retrieval` | No (embed) | Museum + historical-site Qdrant collections |
| `vector_retrieval` | No (embed) | Wikipedia guide chunks |
| `synthesize` | **Yes (#2)** | Source-grounded answer + citations |
| `format` | No | Pass-through (future post-process) |

**LLM calls (typical travel question):** planner + synthesize (= 2). Chitchat uses 1. Guard is a separate small model.

---

## LLM & embedding

```
planner_json / chitchat / synthesize ──▶ llm.generate(prompt)
                                              ├─ gemini → Gemini API (default: gemini-2.5-flash-lite)
                                              └─ qwen   → Ollama (qwen3:1.7b or qwen3:8b)

embeddings (every retrieval) ──▶ Ollama bge-m3
safety_guard               ──▶ Ollama sileader/qwen3guard:0.6b
```

- **Selection:** `.env` → `LLM_PROVIDER=gemini|qwen`
- **Secrets:** `~/.config/rag/.env` → `GEMINI_API_KEY` (`SECRETS_ENV_PATH`)
- **Ollama generation:** `asyncio.Semaphore(1)` (local GPU queue). Gemini is HTTP API; the semaphore applies only on the Ollama path.

---

## State (`GraphState`)

```
question                 str
history                  list[{role, content}]
primary_intent           str   # chitchat|factoid|recommendation|planning|comparison|logistics|meta
intents                  list[{intent, cities}]
prompt_language          str   # ISO 639-1
plan                     {summary, steps[]}
search_queries           list[{q, city?}]      # guide / Wikipedia
deep_search              bool
search_museums           bool
search_artifacts         bool
museum_search_queries    list[{q, city?}]
artifact_search_queries  list[{q, city?}]
is_hotel_search          bool
hotel_search_query       str   # Booking.com URL
is_flight_search         bool
flight_search_query      str   # Kayak URL
needs_graph              bool
graph_queries            list[{template, params}]  # neo4j.md templates
candidate_chunks         list[dict]            # retrieval candidates (reducer: each node appends)
selected_chunks          list[dict]            # evidence passed to synthesis
graph_contexts           list[{city, name?, text, source_url?, source?}]  # neo4j template results
sources                  list[dict]            # final cited sources in answer order [1]…[n]
answer                   str
error                    str | None
guard_label / guard_raw  …
planner_raw              str                   # debug
```

---

## Routing

`retrieval_router` (`edges.py`):

| Condition | Target |
|-----------|--------|
| `error` set | `format` |
| `primary_intent ∈ {chitchat, meta}` | `chitchat` |
| `needs_graph == true` | `graph_retrieval` → then `deep` or `vector` |
| `deep_search == true` | `deep_retrieval` → `vector_retrieval` |
| else | `vector_retrieval` |

`deep_search` only enables museum / historical Qdrant search. Neo4j runs only with `needs_graph` + allowlisted `graph_queries` (no LLM Cypher).

---

## `planner_json` — one LLM, multiple outputs

No separate intent / entity / rewrite nodes. A single call returns JSON:

```jsonc
{
  "primary_intent": "planning",
  "intents": [
    { "intent": "planning", "cities": ["Antalya"] }
  ],
  "prompt_language": "tr",
  "plan": {
    "summary": "I'll build a 3-day museum-focused plan for Antalya.",
    "steps": [
      { "id": "s1", "label": "Search museums", "kind": "museums", "status": "idle" },
      { "id": "s2", "label": "Search guide text", "kind": "vector", "status": "idle" },
      { "id": "s3", "label": "Write the answer", "kind": "synthesize", "status": "idle" }
    ]
  },
  "search_queries": [
    { "q": "Antalya museums and sights", "city": "Antalya" }
  ],
  "deep_search": true,
  "search_museums": true,
  "search_artifacts": false,
  "museum_search_queries": [
    { "q": "Antalya archaeology museum", "city": "Antalya" }
  ],
  "artifact_search_queries": [],
  "is_hotel_search": false,
  "hotel_search_query": "",
  "is_flight_search": false,
  "flight_search_query": "",
  "needs_graph": false,
  "graph_queries": []
}
```

### Search queries

- Each query is `{ "q": "...", "city": "CityName" | "ANY" }`.
- Known `city` applies a Qdrant `city_name` filter; `ANY` / omit → unfiltered.
- Guide queries are English and aligned with corpus themes (landmarks, museums, transport, districts, cuisine…).
- Trip duration (“2 days”, “5 day itinerary”) is a synthesis constraint — usually do not put it in `q`; avoid “top attractions” / “things to do”.
- For itineraries / “what to see” / museum-history asks, the planner is steered to turn on `deep_search` and related POI queries.

### Graph queries (`neo4j.md`)

- Schema + relationships + template list: **[`neo4j.md`](./neo4j.md)** (single reference).
- `needs_graph` + `graph_queries: [{ template, params }]`.
- Template allowlist: `places_in_city`, `day_trips_from_city`, `connection_between_cities`, `airports_serving_city`.
- The LLM does not write Cypher; code runs `graph_store.GRAPH_TEMPLATES`.
- **Target hybrid** (`neo4j.md` Runtime §4): graph candidates → Qdrant name/QID enrich. **Not implemented yet** — graph text context + separate vector queries; bridge is ROADMAP **2.A**.

### Plan `steps[].kind`

`vector | museums | artifacts | graph | external | synthesize`  
The last step is always `synthesize`. Real routing uses `needs_graph` / `deep_search` / `primary_intent`.

---

## Retrieval

### `graph_retrieval_node`

- Only when `needs_graph` and `graph_queries` are non-empty.
- Allowlist template Cypher → rows to text → `graph_contexts` (`source_url` from edge/node; for citations).
- Does not filter Qdrant by graph candidates (see above — `neo4j.md` §4 / 2.A).
- Then `after_graph_router`: `deep_retrieval` if deep, else `vector_retrieval`.

### `deep_retrieval_node`

- Collections: `travel_museums`, `travel_historical_sites` (env).
- `museum_search_queries` / `artifact_search_queries` + city filter.
- Result: `candidate_chunks` (`source_kind` = `museum` / `historical_site`).

### `vector_retrieval_node`

- Collection: `travel_chunks` (Wikipedia guide).
- `search_queries` + city filter.
- Result: `candidate_chunks` (`source_kind` = `guide`).

Both retrieval nodes build the candidate pool via `vector_store.search_candidates`:
Qdrant groups **server-side** by `section_id`, returning
`candidate_sections` distinct sections per query and
`candidates_per_section` chunks from each. There is no “one chunk per section”
hard cap; truncating a flat top-k list later cannot recover a section that never
appeared in that list.

There is **no** global score sort across the candidate pool — comparing cosine
scores from different queries is not meaningful.

### `select_node`

The only selection stage between the candidate pool and synthesis.

- Drops true duplicates by `(collection, chunk_id)`.
- Allocates the budget (`max_selected_chunks`) with two-level round-robin:
  - **lane** = one planner query × one collection → museum/historical/guide
    and each city are represented; one stream cannot consume the whole budget.
  - **section** = within a lane, take one chunk from each section before a
    second chunk from the same section.
- Conditional reranker (`RERANK_MODE` + `RERANK_PROVIDER=bge`):
  - Production: BGE cross-encoder (`BAAI/bge-reranker-v2-m3` via FlagEmbedding).
  - `off` — no scoring
  - `always` — score every time
  - `conditional` — score only when the candidate pool exceeds the budget
  - Provider failure does not stop selection (`rerank_status=fallback`);
    normal skip is recorded separately (`skipped`).
  - The `llm` provider is debug-only; not used in production.
- Presentation order: **museum → historical → guide** (stable sort).
- Result: `selected_chunks` (+ `rerank_status` / `rerank_reason`).

Each candidate carries provenance fields: `collection`, `source_kind`,
`chunk_id`, `section_id`, `section_chunk_seq`, `section_chunk_total`,
`query`, `query_city`, `query_index`.

---

## `synthesize_node`

0. Input is `selected_chunks` (not the full candidate pool) + `graph_contexts`.
1. Merges chunks under the same `section_id` into one citation:
   - `[Name / Section]` heading written once
   - bodies joined in `section_chunk_seq` order
   - skipped sibling chunks marked with `[…]`
2. Presentation order: **museum → historical → guide → graph**.
3. Numbering: `[1]`, `[2]`, … (`[S1]` is gone).
4. Extra instructions by intent (factoid / recommendation / planning / comparison / logistics). Comparison → markdown table (≤5 rows).
5. Grounding: only provided sources; partial-answer policy.
6. If hotel/flight URLs exist, an external-links section is added to the prompt.
7. Output: `answer` + `sources` (same order as citations).

FE: `[n]` in text → link to `sources[n-1].source_url`; Chicago-style **References** below.

---

## WebSocket (`WS /ws/chat`)

**Single-shot:** one question = one connection; server closes after `done` / `error`.

### Client → server

```jsonc
{ "type": "message", "content": "…", "history": [] }
```

### Server → client

| Event | When |
|-------|------|
| `status` | Immediately after connect |
| `plan` | After `planner_json` (`summary`, `steps`) |
| `step` | Retrieval / synthesize progress (`kind`, `status`, `detail`) |
| `done` | Answer + `sources` + planner fields; WS closes |
| `error` | Guard / planner / runtime; WS closes |

Token-by-token stream currently progresses via node updates on `astream(updates)`; the UI shows plan + step + final answer. Markdown (GFM) is rendered in `MessageBubble`.

Rate limit: in-memory per IP (`RATE_LIMIT_*` env).

---

## Intentionally thin / still open

- Separate intent / entity / slot nodes (clarify + out_of_scope exist as routes)
- Groundedness / completeness **loop** (prompt grounding + citation filter exist; no judge loop)
- LLM-generated Cypher (templates only — by design)
- Durable multi-turn memory / anaphora resolver (optional history attach exists)
- Rerank in the default Docker image (`RERANK_PROVIDER=none` until packaging FlagEmbedding/torch); code path exists in `select_node`

Those are the main gaps; the current product is the graph above.

---

## Design notes

- **2 LLM** (planner + synthesize) — cost/latency balance.
- **Plan UX** cuts the blank-screen wait; routing comes from flags.
- **City-aware vector filter** reduces noise.
- **Section allowlist** (ingest) drops commercial noise such as sleep/eat — see README ingest.
- **Citation index = sources list index** — no FE remap.
- Nodes: state in → partial state out; side effects = external services + WS emit.
