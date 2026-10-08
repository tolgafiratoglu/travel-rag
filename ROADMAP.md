# Travel RAG — Roadmap

A locally runnable hybrid (vector + knowledge graph) GraphRAG system that answers Turkish and multilingual travel questions.  
Stack: **Gemini Flash-Lite (API, default)** + **Qwen3 (Ollama, optional)** + **LangGraph** + **FastAPI** + **Qdrant** + **Neo4j** + **Next.js (React, App Router)**.

Time estimates assume part-time work of ~2–3 hours per day.  
Checkboxes reflect **code as of the latest sync** (not the original plan dates).

---

## Current status (summary)

- [x] Docker infrastructure (Ollama, API, Qdrant, Neo4j, Postgres)
- [x] FastAPI + `GET /health` + `WS /ws/chat` (primary transport)
- [x] LLM strategy — `GeminiProvider` / `OllamaProvider`; `LLM_PROVIDER=gemini|qwen`; secrets in `~/.config/rag/.env`
- [x] Embeddings — Ollama **`bge-m3`**
- [x] LangGraph pipeline — intake → guard → planner → route (chitchat / clarify / out_of_scope / graph / deep / vector) → select → synthesize → format
- [x] Neo4j v1 templates + deep POI collections + city-aware Qdrant filter
- [x] Conditional reranker plumbing (`RERANK_MODE` / `RERANK_PROVIDER`; default provider `none` in Docker until FlagEmbedding is in the image)
- [x] Next.js chat UI — plan / step / token stream over WS
- [x] Retrieval eval — Hit@K / Recall@K / MRR (`eval/`)
- [x] Rate limit (per-IP), Qwen3Guard input moderation, grounding prompts

> **Note — transport:** REST `POST /chat` and `web/src/lib/api.ts` were removed. Production client is **WebSocket only**.

> **Note — LLM:** Planner + synthesize default to **Gemini** (`GEMINI_MODEL`). Embeddings always use **Ollama / bge-m3**. Local generation: `LLM_PROVIDER=qwen` (`ollama` is accepted as an alias).

---

## Phase 0 — RAG Hardening  *(mostly done)*

Original goal: observable, fault-tolerant generation path.

- [~] **0.1** REST `ChatResponse` latency fields — **N/A** (REST chat removed; fold into Phase 6 WS/API metrics)
- [x] **0.2** Strategy pattern: `GeminiProvider` + `OllamaProvider`; `LLM_PROVIDER`; `SECRETS_ENV_PATH`
- [ ] **0.2b** Custom exceptions (`LLMTimeoutError`, `LLMUpstreamError`)
- [ ] **0.3** `vector_store` — explicit `top_k` clamp + `min_score` filter (candidate/section pool exists; score floor still open)
- [x] **0.4** Empty input / empty retrieval handling — via intake + synthesize (no `chat.py`)
- [x] **0.5** Numbered citations + “do not invent” — synthesize uses `[1]…[n]` + `GROUNDING_POLICY` (not `[S1]`)
- [ ] **0.6** Request logging middleware (request_id, query length, retrieval count, latency)
- [ ] **0.7** `/health/live` + `/health/ready` (Qdrant + Ollama embed; optional Gemini ping) — only `GET /health` today
- [x] **0.8** `.env.example` + config for `LLM_PROVIDER`, `GEMINI_MODEL`, `SECRETS_ENV_PATH`

**Remaining output:** richer health probes + structured request metrics.

---

## Phase 1 — Working demo (5 node + WebSocket)  *(done; evolved)*

Shipped and extended beyond the original “Qdrant-only linear graph”:

### 1.A Graph skeleton  *(done)*

- [x] **1.1** `graph/state.py` — TypedDict (+ intents, deep/graph flags, selected_chunks, …)
- [x] **1.2** `intake_node` (normalize, history; no LLM)
- [x] **1.3** `planner_json_node` (single LLM → plan + search/graph flags JSON)
- [x] **1.4** `vector_retrieval_node` (+ deep / graph / select stages)
- [x] **1.5** `synthesize_node` + `format_node`
- [x] **1.6** `builder.py` — routed graph (not only linear)

### 1.B WebSocket + streaming  *(done)*

- [x] **1.7** `WS /ws/chat` — single-shot; `status` / `plan` / `step` / `token` / `done` / `error`
- [x] **1.8** Early `status` + `plan` after planner
- [x] **1.9** `stream_generate` for synthesize tokens
- [x] **1.10** Graph stream → WS event mapping (`astream` updates + custom token writer)

### 1.C RAG quality  *(done)*

- [x] **1.11** Citations + grounding + empty-retrieval message
- [x] **1.12** REST test chat — **removed** (see Phase 5.D)

**Also in tree (beyond original Phase 1):** `retrieval_router`, chitchat, clarify, out_of_scope, Neo4j templates, deep POI, select + optional rerank.

---

## Phase 2 — Neo4j (template Cypher)

Design source of truth: **[`neo4j.md`](./neo4j.md)**.

### Done (v1)

- [x] Schema: Country / City / Place / Airport + `IN_COUNTRY` / `IN_CITY` / `SERVED_BY_AIRPORT` / `CONNECTED_BY` / `DAY_TRIP_FROM`
- [x] Ingest: `ingest-wiki-neo4j.py`, `ingest-neo4j-places.py`
- [x] `graph_store.GRAPH_TEMPLATES` (4 templates) + allowlist
- [x] Planner: `needs_graph` + `graph_queries`
- [x] Routing: graph-first when `needs_graph`, then deep/vector
- [x] **2.C** No LLM Cypher (intentional)

### Remaining

- [ ] **2.A** Graph candidates → Qdrant enrich (name / `wikidata_qid` filter) — `neo4j.md` Runtime §4 *(hybrid bridge)*
- [ ] **2.B** Show duration/constraint lines more structurally in synthesize

---

## Phase 3 — Frontend (Next.js / React)  *(core done; polish left)*

### 3.A Scaffold  *(done)*

- [x] **3.1** `web/` — Next.js (TS + Tailwind + App Router)
- [x] **3.2** `next.config.mjs`, `.env.local.example`
- [x] **3.5** FastAPI CORS (`http://localhost:3000`)

### 3.B Chat UI  *(done)*

- [x] **3.6–3.10, 3.13** Layout, `ChatApp`, screens, bubbles, input, chips, theming
- [x] **3.8** `postChat` existed then removed with REST chat (5.11)

### 3.C Remaining UI polish

- [ ] **3.12** Better UX for empty/error/long-wait states
- [ ] **3.14** `web/Dockerfile` + compose web service
- [ ] **3.15** Chat history persistence (disk/DB; in-session history already sent on WS)

### 3.D WebSocket streaming  *(done)*

- [x] **3.16** `status` in UI
- [x] **3.17** FE wired to LangGraph WS events
- [x] **3.19** `lib/ws.ts` + `hooks/useChatStream.ts`
- [x] **3.20** `PlanCard.tsx`
- [x] **3.21** Thinking / step-done line behavior
- [x] **3.22** Token streaming into assistant bubble
- [x] **3.23** Error styling on failed steps

> **Deferred:** City pages (`/city/[slug]`), `robots.ts`, `sitemap.ts` — stretch **S.7**.

---

## Phase 4 — Data Ingestion (Wikipedia + Wikidata)

> Neo4j writes: **[`neo4j.md`](./neo4j.md)**. Scripts under `scripts/ingest-*` + `parse-*`.

### 4.A Ingestion module

- [x] **4.1** Wikipedia city/guide parse → PostgreSQL
- [ ] **4.2** `ingestion/wikidata.py` — SPARQL client (`get_city_metadata(qid)`)
- [x] **4.3** Section-aware chunking
- [x] **4.4** Embed + Qdrant upsert + Neo4j v1
- [x] **4.5** Target cities / POI JSON under `data/wiki/`

### 4.B CLI and orchestration

- [ ] **4.6** Unified `ingestion/run.py` CLI
- [x] **4.7** Idempotent re-run (deterministic chunk_id, MERGE)
- [ ] **4.8** Progress log + error retry
- [ ] **4.9** Documented full pipeline run for 20+ cities (data present; ops runbook polish)

---

## Phase 5 — Guardrails & Safety  *(mostly done; output checks open)*

### 5.A Input guards

- [x] **5.1** Guard interface — `GuardClient` in `services/guard.py` (not named `guardrails.py`)
- [x] **5.2** Length / empty handling — `max_question_chars` + intake (no separate keyword pre-filter)
- [ ] **5.3** Turkish profanity keyword list
- [x] **5.4** Guard on graph path (`safety_guard` before planner; WS uses the graph)

### 5.B Model-based / domain

- [x] **5.5** Model guard — **Qwen3Guard** via Ollama (`sileader/qwen3guard:0.6b`) instead of Llama Prompt Guard
- [x] **5.6** Domain gate — planner `in_scope` (+ `out_of_scope` route)
- [x] **5.7** User-friendly out-of-scope refusal (`out_of_scope_node`)
- [x] **Clarify path** — broad city asks → `needs_clarification` + suggestion chips

### 5.C Output safety

- [ ] **5.8** Automated sensitive-info / hallucination checker on outputs
- [~] **5.9** Ungrounded claims — prompt grounding + citation filter exist; no separate post-hoc claim scorer

### 5.D Transport cleanup  *(done)*

- [x] **5.10** `POST /chat` removed
- [x] **5.11** `web/src/lib/api.ts` removed; WS-only client

---

## Phase 6 — Observability  *(open)*

- [ ] **6.1** `structlog` (JSON logs)
- [ ] **6.2** LLM call metrics (model, prompt/response length, latency)
- [ ] **6.3** Retrieval metrics (query, top_k, max_score, retrieval_time)
- [ ] **6.4** Semaphore queue depth metric
- [ ] **6.5** OpenTelemetry tracing (request → nodes → LLM/retrieval)
- [ ] **6.6** (Optional) Prometheus metrics endpoint
- [ ] **6.7** Error tracking (Sentry or structured log level)

---

## Phase 7 — Quality & Evaluation

- [x] **7.1** `eval/golden/retrieval.json`
- [x] **7.2** `eval/run_retrieval_eval.py` → `eval/results/`
- [x] **7.3** Hit@k / Recall@k / Precision@k / MRR
- [ ] **7.4** Answer quality metric (LLM-as-judge)
- [ ] **7.5** Embedding model comparison (historical `nomic-embed-text` vs current `bge-m3`)
- [x] **7.6** Reranker — `services/reranker.py` (BGE / http / llm); modes `off|always|conditional`; wired in `select_node` + eval `--rerank`  
  *(Docker default `RERANK_PROVIDER=none` until FlagEmbedding/torch is in the image)*
- [ ] **7.7** Chunking strategy A/B (size, overlap)

---

## Phase 8 — Production Polish

### 8.A Reliability

- [~] **8.1** Docker healthchecks — ollama / neo4j / postgres present; api + qdrant still thin
- [ ] **8.2** Graceful API shutdown
- [ ] **8.3** Retry policy (Ollama transient errors)
- [x] **8.4** Rate limiting (per-IP, `RATE_LIMIT_*`)

### 8.B Security

- [ ] **8.5** API key authentication (`X-API-Key` or equivalent)
- [~] **8.6** CORS — localhost configured; env-driven prod origins still open
- [~] **8.7** Neo4j password from env — yes; default `changeme` should not ship to prod
- [x] **8.8** Secrets management — `.env` gitignored; `.env.example`; Gemini key outside repo

### 8.C Documentation

- [x] **8.9** README run locally + `deploy.md` for server
- [ ] **8.10** Richer API/WS docs with examples
- [ ] **8.11** Architecture diagram (Mermaid + rendered PNG)
- [ ] **8.12** Demo video / GIF

### 8.D Deployment

- [~] **8.13** Server setup — see `deploy.md` (keep improving)
- [ ] **8.14** Reverse proxy (nginx/caddy) + TLS (documented pattern; not fully automated)
- [ ] **8.15** Backup strategy (Qdrant + Neo4j volume snapshots)

---

## Stretch Goals

- [~] **S.1** Multi-turn — FE sends history; intake `include_history` heuristics; no durable memory / anaphora resolver
- [ ] **S.2** User preference memory (Neo4j user node)
- [~] **S.3** Trip planning — `planning` intent + day-by-day synthesize; not a separate multi-agent planner
- [ ] **S.4** Image support
- [ ] **S.5** Voice input (Whisper)
- [~] **S.6** Multi-language — answer language follows `prompt_language`; no UI locale toggle yet
- [ ] **S.7** Public city pages — SSG/SSR `/city/[slug]`

---

## Suggested next focus

Priority backlog (honest remaining work):

```
2.A (graph→Qdrant enrich)
  → 0.7 health ready/live + 6.x observability
  → 5.8/5.9 output checks
  → 8.5 API auth + 8.6/8.7 prod secrets/CORS
  → 3.14 web in compose + 7.4 answer eval
  → enable RERANK_PROVIDER=bge in image when torch budget allows
```

---

## Dependency graph (historical)

```
Phase 0 (hardening) ✓ mostly
   ↓
Phase 1 (LangGraph) ✓
   ↓
Phase 2 (Graph KG) ✓ v1 — 2.A/2.B left
   ↓
Phase 3 (Frontend) ✓ core — polish left
   ↓
Phase 4 (Ingestion) ✓ core scripts — CLI/ops polish
   ↓
Phase 5 (Guardrails) ✓ input/domain — output checks left
   ↓
Phase 6 (Observability) ← open
   ↓
Phase 7 (Eval) ✓ retrieval + rerank plumbing — judge/A-B left
   ↓
Phase 8 (Prod polish) ← partial
```

Legend: `[x]` done · `[ ]` open · `[~]` partial / superseded by a different design.
