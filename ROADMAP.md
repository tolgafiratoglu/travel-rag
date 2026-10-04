# Travel RAG — Roadmap

A locally runnable hybrid (vector + knowledge graph) GraphRAG system that answers Turkish and multilingual travel questions.  
Stack: **Gemini 2.5 Flash-Lite (API, default)** + **Qwen3:8b (Ollama, optional)** + **LangGraph** + **FastAPI** + **Qdrant** + **Neo4j** + **Next.js (React, App Router)**.

Time estimates assume part-time work of ~2–3 hours per day.

---

## Current status (summary)

- [x] Docker infrastructure (Ollama, API, Qdrant, Neo4j)
- [x] FastAPI skeleton + `GET /health`
- [x] `llm.py` (Ollama + Semaphore), `embeddings.py`, `vector_store.py`
- [x] `POST /chat` — plain LLM test endpoint (deprecated)
- [x] `WS /ws/chat` — planner graph + status/plan/step/done events
- [x] LangGraph: `intake → planner_json → retrieval_router → vector/chitchat → synthesize → format`
- [x] `seed.py` + `init-ollama.sh`
- [x] Warmup (Ollama), `web/` Next.js chat UI, CORS
- [ ] **LLM strategy** — Gemini (default) + Ollama/Qwen (fallback); secrets in `~/.config/rag/.env` *(see Phase 0.8 below)*

> **Note — `/chat` endpoint:** A **test/Postman** endpoint that makes a plain LLM call. Production transport is **WebSocket**. To be removed in Phase 5.

> **Note — LLM:** Production (planner + synthesize) defaults to **Gemini 2.5 Flash-Lite**. Embeddings always use **Ollama / nomic-embed-text**. Switch back to Qwen with `LLM_PROVIDER=ollama`.

---

## Phase 0 — RAG Hardening  *(~2 days)*

Make the current `/chat` flow observable and fault-tolerant.

- [ ] **0.1** `schemas.py` — add `latency_ms`, `retrieved_count`, `model` fields to `ChatResponse` *(0.5 day)*
- [ ] **0.2** `llm.py` — strategy pattern: `GeminiProvider` + `OllamaProvider`; `LLM_PROVIDER` env; secrets from `SECRETS_ENV_PATH` (`~/.config/rag/.env`) *(0.75 day)*
- [ ] **0.2b** `llm.py` — custom exceptions (`LLMTimeoutError`, `LLMUpstreamError`) *(0.25 day)*
- [ ] **0.3** `vector_store.py` — clamp `top_k` (1..10), `min_score` filter, safe payload defaults *(0.5 day)*
- [ ] **0.4** `chat.py` — empty input validation, empty retrieval handling, exception → HTTP status mapping (400/502/504) *(0.5 day)*
- [ ] **0.5** `chat.py` — numbered sources in the prompt (`[S1] [S2]`), “do not invent” instruction *(0.5 day)*
- [ ] **0.6** Request logging middleware (request_id, query length, retrieval count, latency) *(0.5 day)*
- [ ] **0.7** `health.py` → `/health/live` + `/health/ready` (Qdrant + Ollama embed check; optional Gemini ping) *(0.5 day)*
- [ ] **0.8** `.env.example` + config — `LLM_PROVIDER`, `GEMINI_MODEL`, `SECRETS_ENV_PATH`; document `GEMINI_API_KEY` in `~/.config/rag/.env` *(0.25 day)*

**Output:** Correct status codes in every scenario, measurable latency, cited answers.

---

## Phase 1 — Working demo (5 nodes + WebSocket)  *(~4 days)*

First live version: **5 nodes, 2 LLM calls, Qdrant-only, WS as primary transport.** No separate `intent` / `entity` / `plan` / `rewrite` nodes — all live inside `planner_json_node`.

> Details: `LANGGRAPH.md` → “Phase 1 — Minimal graph”.

### 1.A Graph skeleton  *(~2 days)*

- [ ] **1.1** `graph/state.py` — TypedDict (Phase 1 fields: `question`, `history`, `intent`, `entities`, `assumptions`, `plan`, `search_queries`, `vector_chunks`, `sources`, `answer`, `latency_ms`) *(0.5 day)*
- [ ] **1.2** `graph/nodes.py` — `intake_node` (normalize, history; no LLM) *(0.25 day)*
- [ ] **1.3** `graph/nodes.py` — `planner_json_node` (single LLM: intent + entities + assumptions + plan + search_queries JSON) *(0.75 day)*
- [ ] **1.4** `graph/nodes.py` — `vector_retrieval_node` (search_queries → Qdrant, dedup) *(0.5 day)*
- [ ] **1.5** `graph/nodes.py` — `synthesize_node` + `format_node` *(0.5 day)*
- [ ] **1.6** `graph/builder.py` — linear graph: `intake → planner_json → vector_retrieval → synthesize → format` *(0.25 day)*

### 1.B WebSocket + streaming  *(~1.5 days)*

- [ ] **1.7** `WS /ws/chat` — single-shot; events: `status`, `plan`, `step`, `token`, `done`, `error`; server closes after `done`/`error` *(0.5 day)*
- [ ] **1.8** `status` event — as soon as WS opens (“Analyzing your question…”); `plan` when the planner finishes *(0.25 day)*
- [ ] **1.9** `services/llm.py` — `stream=True` (synthesize tokens) *(0.5 day)*
- [ ] **1.10** LangGraph `astream_events()` adapter — node → WS event mapping *(0.25 day)*

### 1.C RAG quality + test endpoint  *(~0.5 day)*

- [ ] **1.11** Prompt: `[S1]` citations, “do not invent”; empty retrieval handling *(0.25 day)*
- [ ] **1.12** `POST /chat` — test via the graph; **`deprecated` note** (remove in Phase 5) *(0.25 day)*

**Output:** From browser or a WS client: question → status → plan → step → token stream → done. No Neo4j, rerank, router, or self-reflection.

> Deferred (Phase 4+): `intent_classifier`, `decomposition`, `query_rewrite`, `retrieval_router`, `chitchat` path, hard `clarify` ask.

---

## Phase 2 — Neo4j (template Cypher)

Design source of truth: **[`neo4j.md`](./neo4j.md)**.

### Done (v1)

- [x] Schema: Country / City / Place / Airport + `IN_COUNTRY` / `IN_CITY` / `SERVED_BY_AIRPORT` / `CONNECTED_BY` / `DAY_TRIP_FROM`
- [x] Ingest: `ingest-wiki-neo4j.py`, `ingest-neo4j-places.py`
- [x] `graph_store.GRAPH_TEMPLATES` (4 templates) + allowlist
- [x] Planner: `needs_graph` + `graph_queries` (legacy `neo4j_data` fallback)
- [x] Routing: graph-first when `needs_graph`, then deep/vector

### Remaining

- [ ] **2.A** Graph candidates → Qdrant enrich (name / `wikidata_qid` filter) — `neo4j.md` Runtime §4 *(hybrid bridge)*
- [ ] **2.B** Show duration/constraint lines more structurally in synthesize
- [ ] **2.C** No LLM Cypher (intentional; out of `neo4j.md` v1)

> Older Phase 2 items (Attraction/PART_OF city-profile, LLM Cypher) cancelled — `neo4j.md` is authoritative.

---

## Phase 3 — Frontend (Next.js / React)  *(~7 days remaining)*

Single-page RAG chat: the user asks a question and receives plan + step progress + token stream over WebSocket. City pages and SEO are out of scope for this phase.

### 3.A Scaffold  *(~0.5 day — done)*

- [x] **3.1** `web/` — Next.js 14 (TS + Tailwind + App Router) *(0.5 day)*
- [x] **3.2** `next.config.mjs`, `.env.local.example` (`NEXT_PUBLIC_API_BASE_URL`) *(0.25 day)*
- [x] **3.5** FastAPI CORS middleware (`http://localhost:3000`) *(0.25 day)*

### 3.B Chat UI  *(~2 days — done)*

- [x] **3.6** `src/app/layout.tsx` — Server Component, metadata, `h-dvh` *(0.25 day)*
- [x] **3.7** `src/app/page.tsx` — Server Component, `<ChatApp />` *(0.25 day)*
- [x] **3.8** `src/lib/api.ts` — `postChat()` (for test/fallback) *(0.25 day)*
- [x] **3.9** `ChatApp.tsx` — two states (landing / conversation) *(0.5 day)*
- [x] **3.10** `LandingScreen`, `ConversationScreen`, `MessageList`, `MessageBubble`, `ChatInput`, `SuggestionChips` *(0.5 day)*
- [x] **3.13** Basic Tailwind theming, ChatGPT-style input *(0.25 day)*

### 3.C Remaining UI polish  *(~1 day)*

- [ ] **3.12** Better UX for empty/error/long-wait states *(0.25 day)*
- [ ] **3.14** `web/Dockerfile` + `docker-compose.yml` web service *(0.5 day)*
- [ ] **3.15** Chat history persistence (after multi-turn) *(0.25 day)*

### 3.D WebSocket streaming — primary transport  *(~3.5 days)*

> Event table and UI behavior: `LANGGRAPH.md` → “Chain-of-Thought UX”.

#### 3.D.1 Server  *(overlaps Phase 1.B — should be done once FE is wired)*

- [ ] **3.16** Show `status` event in the UI (server side in Phase 1.B) *(0.25 day)*
- [ ] **3.17** Integration-test LangGraph event adapter with FE *(0.25 day)*

#### 3.D.2 Client  *(~2 days)*

- [ ] **3.19** `web/src/lib/ws.ts` + `hooks/useChatStream.ts` — single-shot WS; event router (`status` / `plan` / `step` / `token` / `done` / `error`) *(0.75 day)*
- [ ] **3.20** `PlanCard.tsx` — `status` line + verbal `summary` + `steps[]` checklist *(0.5 day)*
- [ ] **3.21** Thinking-line behavior — grey “Thinking…” on `step:running`; replace with `step:done` payload (`label`, `detail`) *(0.25 day)*
- [ ] **3.22** Token streaming — append `token` events to the assistant bubble; remove Thinking line on first token *(0.25 day)*
- [ ] **3.23** Error state — last `step:running` line turns red + assistant bubble: “Sorry, something went wrong at step …” *(0.25 day)*

**Output:** User asks a question; ChatGPT-like “Thinking → step finding → answer streaming”; WS closes when the question finishes.

> **Deferred:** City pages (`/city/[slug]`), `robots.ts`, `sitemap.ts` — stretch goal **S.7** or after ingestion.

---

## Phase 4 — Data Ingestion (Wikipedia + Wikidata)  *(~7 days)*

Replace seed data with real content.

> Neo4j writes: **[`neo4j.md`](./neo4j.md)** — nodes `Country` / `City` / `Place` / `Airport`; no prose/`SourceChunk`. Current path: `scripts/ingest-wiki-neo4j.py`, `scripts/ingest-neo4j-places.py` (+ Qdrant `scripts/ingest-wiki-qdrant.py`).

### 4.A Ingestion module  *(~5 days)*

- [x] **4.1** Wikipedia city/guide parse → PostgreSQL (`scripts/parse-wiki-pages.py`, `data/wiki/sources.json`)
- [ ] **4.2** `ingestion/wikidata.py` — SPARQL client (`get_city_metadata(qid)`) *(1 day)*
- [x] **4.3** Section-aware chunking (`scripts/lib/wp_chunks.py` + Unstructured)
- [x] **4.4** Embed + Qdrant upsert + Neo4j v1 (`neo4j.md`) — `scripts/ingest-*` (City/Country/Place/`IN_*`/`CONNECTED_BY`/`DAY_TRIP_FROM`; **no SourceChunk**)
- [x] **4.5** Target cities — `data/wiki/sources.json` (+ museums/historical JSON)

### 4.B CLI and orchestration  *(~2 days)*

- [ ] **4.6** `ingestion/run.py` — CLI (`python -m ingestion.run --config sources.yaml`) *(0.5 day)*
- [x] **4.7** Idempotent re-run (deterministic chunk_id, MERGE-based)
- [ ] **4.8** Progress log + error retry *(0.5 day)*
- [ ] **4.9** Run full pipeline for first 20 cities *(0.5 day)*

**Output:** System populated with real Wikipedia data for 20+ cities.

---

## Phase 5 — Guardrails & Safety  *(~4 days)*

Protect against malicious / out-of-domain queries.

### 5.A Interface and rule-based  *(~1 day)*

- [ ] **5.1** `services/guardrails.py` — `GuardrailService.check_input()` interface *(0.25 day)*
- [ ] **5.2** Rule-based pre-filter (empty, too long, suspicious keywords) *(0.25 day)*
- [ ] **5.3** Turkish profanity keyword list *(0.25 day)*
- [ ] **5.4** Guardrail integration into `chat.py` and `ws/chat` *(0.25 day)*

### 5.B Model-based guards  *(~2 days)*

- [ ] **5.5** Llama Prompt Guard 2-86M integration *(1 day)*
- [ ] **5.6** Domain classifier (mini-prompt on active LLM: “is this a travel question?”) *(0.5 day)*
- [ ] **5.7** User-friendly refusal for out-of-domain queries *(0.5 day)*

### 5.C Output safety  *(~1 day)*

- [ ] **5.8** Basic check for sensitive info / hallucination in LLM answers *(0.5 day)*
- [ ] **5.9** Flag ungrounded claims (“not supported by sources”) *(0.5 day)*

### 5.D Transport cleanup  *(~0.5 day)*

- [ ] **5.10** Remove `POST /chat` — client uses WebSocket only *(0.25 day)*
- [ ] **5.11** Delete `web/src/lib/api.ts` (`postChat`); keep only `lib/ws.ts` *(0.25 day)*

**Output:** Attacks like “ignore previous instructions” are blocked; off-topic questions are refused politely; REST chat surface is fully gone.

---

## Phase 6 — Observability  *(~3 days)*

See what is happening in production.

- [ ] **6.1** `structlog` integration (JSON logs) *(0.5 day)*
- [ ] **6.2** LLM call metrics (model, prompt length, response length, latency) *(0.5 day)*
- [ ] **6.3** Retrieval metrics (query, top_k, max_score, retrieval_time) *(0.25 day)*
- [ ] **6.4** Semaphore queue depth metric *(0.25 day)*
- [ ] **6.5** OpenTelemetry tracing (request → graph nodes → LLM/retrieval spans) *(1 day)*
- [ ] **6.6** (Optional) Prometheus metrics endpoint *(0.5 day)*
- [ ] **6.7** Error tracking (Sentry or log level) *(0.25 day)*

**Output:** When a user says “it’s slow,” you can see which step lost time.

---

## Phase 7 — Quality & Evaluation  *(~5 days)*

Make retrieval and answer quality measurable.

- [x] **7.1** `eval/golden/retrieval.json` — retrieval golden (source_url + section; extensible)
- [x] **7.2** `eval/run_retrieval_eval.py` — Hit@k / Recall@k / MRR → `eval/results/`
- [x] **7.3** Retrieval metrics (Hit@k, set Recall@k, Precision@k, MRR)
- [ ] **7.4** Answer quality metric (LLM-as-judge, simple scoring) *(0.5 day)*
- [ ] **7.5** Compare embedding models (`nomic-embed-text` vs `bge-m3`) *(1 day)*
- [ ] **7.6** Try a reranker (cross-encoder, optional) *(0.5 day)*
- [ ] **7.7** Chunking strategy A/B test (chunk size, overlap) *(0.5 day)*

**Output:** “Did this change improve quality?” is answered with numbers.

---

## Phase 8 — Production Polish  *(~6 days)*

Final steps from demo to production.

### 8.A Reliability  *(~1.5 days)*

- [ ] **8.1** Docker healthchecks for all services *(0.25 day)*
- [ ] **8.2** Graceful API shutdown *(0.25 day)*
- [ ] **8.3** Retry policy (Ollama transient errors) *(0.5 day)*
- [ ] **8.4** Rate limiting (per-IP) *(0.5 day)*

### 8.B Security  *(~1.5 days)*

- [ ] **8.5** API key authentication (`X-API-Key` header) *(0.5 day)*
- [ ] **8.6** CORS configuration *(0.25 day)*
- [ ] **8.7** Neo4j password from env (remove hardcoded `changeme`) *(0.25 day)*
- [ ] **8.8** Secrets management (`.env` not in git; add `.env.example`) *(0.5 day)*

### 8.C Documentation  *(~1.5 days)*

- [ ] **8.9** README “Run locally” / “Deploy to server” sections *(0.5 day)*
- [ ] **8.10** Enrich API docs with examples *(0.25 day)*
- [ ] **8.11** Architecture diagram (Mermaid + rendered PNG) *(0.5 day)*
- [ ] **8.12** Demo video / GIF *(0.25 day)*

### 8.D Deployment  *(~1.5 days)*

- [ ] **8.13** Server setup instructions (Docker, GPU drivers, etc.) *(0.5 day)*
- [ ] **8.14** Reverse proxy (nginx/caddy) + TLS *(0.5 day)*
- [ ] **8.15** Backup strategy (Qdrant + Neo4j volume snapshots) *(0.5 day)*

**Output:** Project deployed on a server, reachable externally, documented.

---

## Stretch Goals  *(~2–4 days each)*

If time remains or to make the project stand out:

- [ ] **S.1** Multi-turn conversation (chat history context) *(2 days)*
- [ ] **S.2** User preference memory (user node in Neo4j) *(3 days)*
- [ ] **S.3** Trip planner agent (multi-step planning) *(4 days)*
- [ ] **S.4** Image support (photos on chunks, shown in frontend) *(3 days)*
- [ ] **S.5** Voice input (Whisper) *(2 days)*
- [ ] **S.6** Multi-language UI (English + Turkish toggle) *(2 days)*
- [ ] **S.7** Public city pages — SSG/SSR `/city/[slug]` (`generateStaticParams`, JSON-LD, render from ingestion data) *(4 days)*

---

## Total estimated time

| Phase | Duration |
|-------|----------|
| Phase 0 — RAG Hardening | ~2 days |
| Phase 1 — Demo (5 nodes + WS) | ~4 days |
| Phase 2 — Neo4j (template Cypher) | ~4 days |
| Phase 3 — Frontend (Next.js) | ~7 days remaining (~2.5 days done) |
| Phase 4 — Data Ingestion | ~7 days |
| Phase 5 — Guardrails & Safety | ~4.5 days |
| Phase 6 — Observability | ~3 days |
| Phase 7 — Quality & Evaluation | ~5 days |
| Phase 8 — Production Polish | ~6 days |
| **Total (core)** | **~45 days** |
| Stretch Goals (all) | +20 days |

Part-time (~2–3 h/day): core ~7–9 weeks; with stretch ~11–13 weeks.

---

## Suggested orderings

**Quality-first:**

```
0 → 1 → 2 → 5 (simple guardrails) → 3 → 4 → 6 → 7 → 8
```

**Demo-first (fast visual result):**

```
0 → 1 (graph + WS) → 3.D (wire FE WS) → 2 (Neo4j) → 4 (ingestion) → 5 → 6 → 7 → 8
```

(3.B UI ✓. Next: Phase 1 — 5-node graph + WS backend, then wire 3.D FE to WS.)

---

## Dependency graph

```
Phase 0 (hardening)
   ↓
Phase 1 (LangGraph) ──┐
   ↓                  │
Phase 2 (Graph KG)    │
   ↓                  ↓
   └──────────► Phase 3 (Frontend) ──┐
                                     │
Phase 4 (Ingestion) ─────────────────┤
                                     ↓
Phase 5 (Guardrails) ──► Phase 6 (Observability) ──► Phase 7 (Eval) ──► Phase 8 (Prod)
```

Phase 3 and Phase 4 are independent and can proceed in parallel.
