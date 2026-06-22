# Travel RAG

A multi-step Retrieval-Augmented Generation system for travel queries.  
Combines **vector search** (Qdrant) with a **knowledge graph** (Neo4j), orchestrated by **LangGraph** and powered by **Qwen** on Ollama.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Client Layer (Next.js — React)                      │
│                                                                     │
│   Browser — single-page RAG chat (`/`)                               │
│   WS /ws/chat ─────────────────────────────┐  (primary transport)   │
│                                            │                        │
└────────────────────────────────────────────┼────────────────────────┘
                                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                             │
│                                                                     │
│   WS /ws/chat (primary)     POST /chat (internal test only)          │
│                 │                            │                      │
│                 ▼                            ▼                      │
│            ┌────────────────────────────────────┐                   │
│            │    asyncio.Semaphore(1) Queue      │  ← max 1 LLM call│
│            └────────────────────────────────────┘                   │
└─────────────────────────────┬───────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Orchestration (LangGraph)                           │
│                                                                     │
│   ┌──────────┐    ┌─────────────────┐                              │
│   │   Plan   │───▶│ stream to user  │  (WebSocket: instant feedback)│
│   │ (LLM #1) │    │  via WS event   │                              │
│   └────┬─────┘    └─────────────────┘                              │
│        │                                                            │
│        ▼                                                            │
│   ┌──────────────┐    ┌──────────────────┐                         │
│   │  Classify /  │───▶│ Parallel         │                         │
│   │    Route     │    │ Retrieval        │                         │
│   └──────┬───────┘    └────────┬─────────┘                         │
│          │                     │                                    │
│   (chitchat) ┌──────────────┐  │  ┌──────────────┐                 │
│          ▼   │              │  │  │              │                 │
│   ┌──────────┴───┐  ┌───────▼──┴──┐  ┌─────────────┐               │
│   │   Direct     │  │   Vector    │  │    Graph    │               │
│   │  Synthesize  │  │  Retrieval  │  │  Retrieval  │               │
│   └──────┬───────┘  └──────┬──────┘  └──────┬──────┘               │
│          │                 └────────┬───────┘                       │
│          │                          ▼                               │
│          │           ┌───────────────────────────┐                  │
│          │           │ Rerank → Synthesize       │                  │
│          │           │ (LLM #2, token stream)    │                  │
│          │           └─────────────┬─────────────┘                  │
│          └───────────────────┬─────┘                                │
│                              ▼                                      │
│                       final answer                                  │
└─────────────────────────────────────────────────────────────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
┌────────────────┐   ┌────────────────┐   ┌────────────────────────┐
│     Qdrant     │   │     Neo4j      │   │   Ollama (shared)      │
│  Vector Store  │   │ Knowledge Graph│   │  ├─ qwen-shared (gen)  │
│  :6333         │   │  :7474/:7687   │   │  └─ nomic-embed-text   │
└────────────────┘   └────────────────┘   └────────────────────────┘
```

---

## Why This Architecture

### Why LangGraph (not a simple chain)?

A naive RAG does: embed query → retrieve → stuff into prompt → generate.  
This fails for complex travel questions like:

> "Ailece, bütçe dostu, Akdeniz ikliminde, tarih ve deniz birlikte olan bir yer öner"

This question has multiple constraints (family, budget, climate, history, sea). A single retrieval pass won't capture all dimensions. LangGraph lets us:

1. **Plan first** — produce a quick "what I'll do" summary streamed instantly to the user
2. **Decompose** the query into sub-questions
3. **Route** each sub-question to the right retrieval source (vector vs graph)
4. **Validate** the answer and loop back if insufficient
5. **Stream** intermediate steps to the user in real-time

### Why Two-Phase Reasoning (Plan → Execute)?

A naive RAG makes the user stare at an empty screen for 5-30 seconds. Instead, we use a two-phase pattern:

1. **Plan phase** (~1 sn) — Qwen produces a short "I'll look at X and Y" summary, streamed immediately via WebSocket. The user sees activity within ~1 second.
2. **Execute phase** (5-30 sn) — Retrieval (vector / graph) and final synthesis, streamed token-by-token.

Benefits:
- Perceived latency drops dramatically — empty screen problem disappears
- User sees what the AI understood and can correct early
- Plan-then-execute pattern is more debuggable
- Plan output also drives routing (`query_type` → which retrieval path to take)

Trade-off: One extra LLM call (~1-2 sn added to total latency), in exchange for vastly better UX.

### Chain-of-Thought Style Streaming (visible progress, not internal monologue)

The UI does **not** expose the model's raw internal reasoning. Instead, the LangGraph pipeline emits human-readable progress over WebSocket so the user sees what the system is doing in real time:

1. **`status` event** — immediately after WS opens: "Sorunu analiz ediyorum…" (no LLM wait)
2. **`plan` event** — after `planner_json_node`: verbal summary + structured `steps[]` (see `LANGGRAPH.md`)
3. **`step` events** — each meaningful node emits `running` (grey "Thinking…") and `done` (finding text)
4. **`token` events** — final synthesis streams into assistant bubble; thinking line removed on first token
5. **`done` event** — final answer + sources; **server closes WebSocket**

Each user question opens a **new single-shot WebSocket** that closes on `done` or `error`. This keeps backpressure simple and matches the Semaphore(1) model.

### Why Next.js (React) for the Frontend?

The chat UI is interactive — message list, input, loading states, and later token streaming. React (via Next.js App Router) gives:

- **Component model** — `ChatWindow`, `MessageBubble`, `ChatInput` as isolated pieces; streaming/plan UI added later without a rewrite
- **Server + client split** — `page.tsx` stays a Server Component; only `ChatWindow` is `"use client"` (state + API calls)
- **Room to grow** — same project can later add SSR/SSG routes (e.g. public destination pages) when needed; chat does not require SEO today
- **Local dev first** — `npm run dev` on `:3000` talks to FastAPI on `:8000`; Docker for `web` comes in a later step

Current scope: **one route (`/`), sync `POST /chat`** — interim until WebSocket lands. Once `WS /ws/chat` is in place, `POST /chat` becomes a Postman-only test endpoint and is scheduled for removal in Phase 5.

### Why Hybrid Retrieval (Vector + Knowledge Graph)?

| Approach | Good at | Bad at |
|----------|---------|--------|
| Vector search | Semantic similarity, fuzzy matching, natural language | Structured relationships, multi-hop reasoning |
| Knowledge graph | Entity relationships, exact constraints, graph traversal | Fuzzy matching, understanding context |

Example: "Istanbul'a yakın, UNESCO listesindeki yerler"
- **Vector search** finds chunks mentioning UNESCO near Istanbul
- **Graph search** does: `(Istanbul)-[:NEAR]->(city)-[:HAS]->(site {unesco: true})`
- **Combined** gives better recall and precision

### Why Semaphore-based Concurrency Control?

Qwen on Ollama processes one prompt at a time efficiently. Concurrent requests to Ollama cause:
- Context switching overhead
- Memory pressure (each prompt loads into VRAM)
- Unpredictable latency spikes

`asyncio.Semaphore(1)` provides:
- Guaranteed serial LLM access
- FIFO fairness across users
- Zero external dependency (no Redis/RabbitMQ needed)
- Observability (queue depth metric)

### Why a Separate Ingestion Module?

Data ingestion (fetching from Wikivoyage/Wikidata APIs, chunking, embedding, loading) is an offline batch process — separate from the online query pipeline. Keeping it in its own Python module (`ingestion/`) provides:

- Clear separation of concerns (batch vs real-time)
- Shared dependencies with the API (same Qdrant/Neo4j/Ollama clients)
- Unified language — no context switching, single `pyproject.toml`
- Can be run as a CLI script independently of the API server

---

## Data Flow

### Ingestion (offline, batch)

```
Wikivoyage API ──┐
                 ├──▶ Ingestion Pipeline (Python) ──┬──▶ Embed ──▶ Qdrant
Wikidata SPARQL ─┘       (fetch → chunk)            │
                                                    └──▶ Entity Extract ──▶ Neo4j
```

### Query (online, per-request)

```
User Question
     │
     ▼
Query Analysis (LLM call #1: classify complexity)
     │
     ├─ simple ──▶ embed query ──▶ Qdrant top-k ──▶ rerank ──▶ synthesize
     │
     └─ complex ──▶ Sub-Question Gen (LLM call #2)
                        │
                        ├──▶ Vector retrieval (embed each sub-q)
                        └──▶ Graph retrieval (LLM call #3: generate Cypher)
                                    │
                                    ▼
                              Merge + Rerank
                                    │
                                    ▼
                          Synthesize (LLM call #4)
                                    │
                                    ▼
                          Validate (LLM call #5: is answer complete?)
                                    │
                                    ├─ yes ──▶ return to user
                                    └─ no ──▶ loop (max 2 iterations)
```

Note: Each "LLM call" passes through the Semaphore sequentially, even within a single user's request. This is by design — Qwen processes one prompt at a time.

---

## Knowledge Graph Schema

```
(:Country {name, continent, currency, language})
     ▲
     │ :IN
     │
(:City {name, population, lat, lon, description})
     │
     ├──[:BEST_SEASON]──▶ (:Season {name, months})
     ├──[:BUDGET_LEVEL]──▶ (:Budget {level: "budget|mid|luxury"})
     ├──[:CONNECTED_TO {mode, duration, cost_level}]──▶ (:City)
     │
     ▼ :LOCATED_IN
     │
(:Attraction {name, type, description, unesco: bool})
(:Restaurant {name, cuisine, price_range})
(:Hotel {name, stars, price_range})
(:Activity {name, category, season_dependent: bool})
```

This schema enables queries like:
- Path finding: cheapest route between two cities
- Constraint satisfaction: cities matching multiple criteria
- Recommendation: "similar to X but with Y"

---

## Tech Stack

| Layer | Technology | Version | Why |
|-------|-----------|---------|-----|
| LLM | Qwen 3 8B (via Ollama) | qwen3:8b | Good multilingual, runs local |
| Embeddings | nomic-embed-text | v1.5 | 768d, 8K context, Ollama-native |
| Vector DB | Qdrant | latest | Rust-based, fast, good filtering |
| Graph DB | Neo4j Community | 5.x | Industry standard, Cypher is expressive |
| Orchestration | LangGraph | 0.2+ | State machines for LLM workflows |
| API | FastAPI | 0.115+ | Async-native, WebSocket support, auto-docs |
| Ingestion | Python (httpx + asyncio) | — | Shared language, same Qdrant/Neo4j/Ollama clients as API |
| Frontend | Next.js (App Router) + React + TypeScript + Tailwind | 14+ | Single-page RAG chat; Server `page.tsx` + client `ChatWindow` |
| Infra | Docker Compose | v2 | Multi-service local dev |

---

## Project Structure

```
travel-rag/
├── docker-compose.yml          # All services (Ollama, API, Qdrant, Neo4j)
├── .env.example                # Environment template
├── README.md                   # This file
│
├── api/                        # Python - FastAPI Backend (JSON + WS only)
│   ├── Dockerfile
│   ├── pyproject.toml          # Dependencies (uv/pip)
│   ├── app/
│   │   ├── main.py             # App factory, lifespan, CORS
│   │   ├── config.py           # Pydantic Settings (env-based)
│   │   ├── routes/
│   │   │   ├── chat.py         # WS /ws/chat (primary) + POST /chat (test only, deprecated)
│   │   │   └── health.py       # GET /health/live + /health/ready
│   │   ├── services/
│   │   │   ├── llm.py          # Ollama client + Semaphore(1)
│   │   │   ├── embeddings.py   # nomic-embed-text wrapper
│   │   │   ├── vector_store.py # Qdrant CRUD
│   │   │   └── graph_store.py  # Neo4j driver + Cypher execution
│   │   ├── graph/              # LangGraph pipeline
│   │   │   ├── state.py        # TypedDict state schema
│   │   │   ├── nodes.py        # Node implementations
│   │   │   ├── edges.py        # Routing logic
│   │   │   └── builder.py      # Compile graph
│   │   └── models/
│   │       ├── schemas.py      # API request/response models
│   │       └── entities.py     # Domain models
│   └── tests/
│
├── web/                        # Next.js 14 App Router (React + TS + Tailwind)
│   ├── package.json
│   ├── next.config.mjs
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── postcss.config.mjs
│   ├── .env.local.example      # NEXT_PUBLIC_API_BASE_URL
│   └── src/
│       ├── app/
│       │   ├── layout.tsx      # Server Component — metadata
│       │   ├── page.tsx        # Server Component — renders ChatWindow
│       │   └── globals.css
│       ├── components/chat/
│       │   ├── ChatApp.tsx     # "use client" — state, started?
│       │   ├── LandingScreen.tsx
│       │   ├── ConversationScreen.tsx
│       │   ├── ChatInput.tsx
│       │   ├── MessageList.tsx
│       │   ├── MessageBubble.tsx
│       │   └── SuggestionChips.tsx
│       ├── hooks/
│       │   └── useChatStream.ts   # WS connection, plan + step + token events
│       └── lib/
│           ├── ws.ts            # WebSocket helper (primary)
│           └── api.ts           # postChat() — test/fallback only
│
├── ingestion/                  # Python - Data Ingestion Pipeline
│   ├── __init__.py
│   ├── wikivoyage.py           # MediaWiki API client + parser
│   ├── wikidata.py             # SPARQL query builder + client
│   ├── chunker.py              # Section-based text chunking
│   ├── loader.py               # Bulk insert to Qdrant + Neo4j
│   ├── run.py                  # CLI entrypoint (python -m ingestion.run)
│   └── configs/
│       └── sources.yaml        # Target cities/regions
│
└── scripts/
    ├── seed_data.py            # Insert test travel chunks
    └── reset_db.sh             # Wipe Qdrant + Neo4j data
```

---

## Docker Network Topology

```
Host
  │
  ├── :3000  web (local: npm run dev — not in docker-compose yet)
  │              WS  ws://localhost:8000/ws/chat   (primary)
  │              POST http://localhost:8000/chat   (test only)
  │
  └── :8000  api ──┐
                   ▼
┌─────────────────────────────────────────────────────────┐
│                 llm_internal (docker network)            │
│                                                         │
│  ┌──────────────┐  ┌───────┐  ┌───────┐  ┌─────────┐  │
│  │   ollama     │  │  api  │  │qdrant │  │  neo4j  │  │
│  │  (internal)  │  │ :8000 │  │ :6333 │  │:7474/87 │  │
│  │  dns: ollama │  │       │  │       │  │         │  │
│  └──────────────┘  └───────┘  └───────┘  └─────────┘  │
└─────────────────────────────────────────────────────────┘
```

Backend services run via `docker compose`. The `web` frontend runs locally with `npm run dev` for now; a `web` Docker service will be added later.

Ollama has no exposed port — only reachable within `llm_internal` network.

---

## Run Locally

### Backend (Docker)

```bash
docker compose up -d
# API: http://localhost:8000/health
```

### Frontend (local)

```bash
cd web
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
# UI: http://localhost:3000
```

Ask a travel question in the chat UI; answers come from `POST /chat` (vector retrieval + Qwen). First response may be slow while the LLM warms up.

After changing CORS or API code, rebuild the API container: `docker compose up -d --build api`.

---

## Implementation Phases

### Phase 1: Working Demo (LangGraph + WebSocket)
- [x] Docker Compose, FastAPI, LLM, embeddings, Qdrant, seed data
- [ ] **5-node graph**: `intake → planner_json → vector_retrieval → synthesize → format` (see `LANGGRAPH.md`)
- [ ] `planner_json_node` — single LLM call: intent + entities + assumptions + plan + search_queries
- [ ] `WS /ws/chat` — events: `status`, `plan`, `step`, `token`, `done`, `error`; single-shot per question
- [ ] `POST /chat` — test/Postman only (`deprecated`; removed Phase 5)

### Phase 2: Neo4j (template Cypher)
- [ ] Graph store service
- [ ] Template Cypher (distance, UNESCO, airport) — no LLM Cypher yet
- [ ] Hybrid merge (vector + graph)
- [ ] LLM Cypher generation (Phase 4, optional)

### Phase 3: Data Ingestion (Python)
- [ ] Ingestion module scaffold
- [ ] Wikivoyage MediaWiki API client
- [ ] Wikidata SPARQL client
- [ ] Section-based chunker
- [ ] Bulk loader (Qdrant + Neo4j)
- [ ] CLI entrypoint with config-driven city list

### Phase 4: Frontend (Next.js / React)
- [x] Next.js 14 App Router scaffold (TS + Tailwind)
- [x] FastAPI CORS for `http://localhost:3000`
- [x] Two-state UI (landing + conversation), `ChatApp` client component
- [ ] `useChatStream` hook — single-shot WebSocket (`status` / `plan` / `step` / `token` / `done`)
- [ ] Plan card (verbal summary + steps[] rendered as checklist)
- [ ] Thinking steps UI — grey "Thinking…" line on `step:running`, replaced by `detail` on `step:done`
- [ ] Token streaming into assistant bubble; thinking line removed on first token
- [ ] Error state — failed step turns red + assistant error message
- [ ] Source citations (`sources` from `done` event)
- [ ] `web/Dockerfile` + `docker-compose.yml` web service
- [ ] Queue position indicator

### Phase 5: Guardrails & Safety
- [ ] GuardrailService interface (`check_input` → allow/deny)
- [ ] Rule-based pre-filter (empty, too long, keyword blocklist)
- [ ] Türkçe profanity keyword list
- [ ] Llama Prompt Guard 2-86M (prompt injection / jailbreak detection)
- [ ] Domain classifier (seyahat sorusu mu? — Qwen mini-prompt veya mDeBERTa zero-shot)
- [ ] Output safety check (hallucination / toxic response filtering)
- [ ] **Remove `POST /chat`** — WebSocket becomes the only client transport

### Phase 6: Production Polish
- [ ] Structured logging (structlog)
- [ ] OpenTelemetry tracing (request → LLM → retrieval spans)
- [ ] Error handling + graceful degradation
- [ ] API authentication (API key based)
- [ ] Docker health checks for all services
- [ ] Architecture diagram in repo (Mermaid + rendered PNG)

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM concurrency | Semaphore(1), not message queue | Simple, no external deps, sufficient for single-GPU Ollama |
| Retrieval strategy | Hybrid (vector + graph) | Covers both semantic similarity and structured relationships |
| LangGraph (Phase 1) | 5-node linear graph, 2 LLM calls | MVP before expanding to full target architecture in `LANGGRAPH.md` |
| Embedding location | Same Ollama instance | Operational simplicity; nomic-embed-text is lightweight |
| Ingestion language | Python | Shared deps with API, single language across project |
| Frontend approach | Next.js (App Router) + React | Server `page.tsx` + client `ChatApp`; landing/conversation two-state UI |
| Client transport | WebSocket (primary), single-shot per question | Required for plan + step + token streaming; REST `/chat` kept only as a Postman test endpoint, removed in Phase 5 |
| Chain-of-thought UX | Visible plan + per-node step events, not raw model thoughts | Safer, debuggable, and matches plan-first reasoning |
| Graph DB | Neo4j Community | Cypher is powerful; free community edition sufficient |
| Package manager | uv (Python) | Fast, modern replacement for pip/poetry |

---

## Environment Variables

```env
# Ollama (shared service on llm_internal network)
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=qwen-shared
OLLAMA_EMBED_MODEL=nomic-embed-text

# Qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=travel_chunks

# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme

# API
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=info

# Web (local dev — copy to web/.env.local)
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

---

## Learning Path (Concepts to Master)

As you build each phase, these are the core AI Engineering concepts involved:

1. **Embeddings & Vector Search** — how text becomes numbers, similarity metrics (cosine, dot product), HNSW index trade-offs
2. **Chunking Strategies** — why chunk size matters, overlap rationale, metadata preservation
3. **Knowledge Graphs for RAG** — entity extraction, relationship modeling, Cypher query generation
4. **Agentic RAG** — query decomposition, self-reflection loops, tool use patterns
5. **LLM Orchestration** — state machines vs DAGs, streaming, error recovery
6. **Concurrency in AI Systems** — backpressure, queue management, GPU scheduling
7. **Retrieval Quality** — recall vs precision, reranking, evaluation metrics (MRR, NDCG)
8. **Observability** — tracing LLM calls, token usage tracking, latency percentiles
