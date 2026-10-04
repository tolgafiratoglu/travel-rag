# Travel RAG

A multi-step Retrieval-Augmented Generation system for travel questions.  
Orchestrated with **LangGraph**, vector search with **Qdrant**, structured candidates/constraints with **Neo4j** (schema: [`neo4j.md`](./neo4j.md)), and a **Next.js** chat UI.

| Role | Technology |
|------|------------|
| Generation (default) | Gemini `gemini-2.5-flash-lite` |
| Generation (local) | Qwen via Ollama (`LLM_PROVIDER=qwen`) |
| Embeddings | Ollama **`bge-m3`** |
| Safety guard | Ollama `sileader/qwen3guard:0.6b` |
| Vector DB | Qdrant (`travel_chunks`, `travel_museums`, `travel_historical_sites`) |
| Graph DB | Neo4j 5 — `IN_*` / `CONNECTED_BY` / `DAY_TRIP_FROM` (see `neo4j.md`) |
| API | FastAPI + WebSocket |
| UI | Next.js App Router + React + Tailwind |

Pipeline: **[`LANGGRAPH.md`](./LANGGRAPH.md)**. Graph schema: **[`neo4j.md`](./neo4j.md)**.

---

## Architecture (overview)

```
Browser (Next.js :3000)
    │  WS /ws/chat  (one question = one connection)
    ▼
FastAPI (:8000)
    │
    ▼
LangGraph
  intake → safety_guard → planner_json
              │
              ├─ chitchat/meta → chitchat → format
              ├─ needs_graph → Neo4j templates → (± deep) → Wikipedia guide → synthesize
              ├─ deep_search → museums/historical → Wikipedia guide → synthesize
              └─ else → Wikipedia guide → synthesize
    │
    ├── Qdrant (guide + optional POI collections)
    ├── Neo4j (candidates / constraints — neo4j.md)
    ├── Gemini or Ollama/Qwen (generation)
    └── Ollama (embeddings + guard)
```

Two-phase UX: plan summary first (WS `plan`), then retrieval steps (`step`), then a cited answer (`done`). Citation format `[1]`, `[2]`, …; the frontend lists references in Chicago style.

---

## Why this design?

- **LangGraph** — plan → route → retrieve → synthesize instead of a single chain; separate paths for chitchat and deep POI.
- **Plan-first WS** — users are not left on a blank screen; the system shows what it will do early.
- **City-aware Qdrant filter** — the planner pins searches with `SearchQuery { q, city }` on city metadata (`ANY` = no filter).
- **Neo4j templates** — constraints such as day trips / connections / airports; no LLM-generated Cypher (`needs_graph` + allowlist).
- **Deep search** — museum / historical-site collections; `deep_search` turns them on.
- **Section allowlist (ingest)** — See / Do / Get in, etc.; skips commercial noise like Sleep / Eat.
- **Ollama Semaphore(1)** — GPU queue for local Qwen generation only; Gemini uses HTTP.

---

## Data flow

### Ingest (offline)

```
data/wiki/sources.json ──▶ scripts/parse-wiki-pages.py ──▶ PostgreSQL wp_pages (+ wp_gov_links *.gov.*)
                              ├─▶ scripts/ingest-wiki-qdrant.py ──▶ Qdrant travel_chunks
                              └─▶ scripts/ingest-wiki-neo4j.py  ──▶ Neo4j City/Country + relationships

data/wiki/museums.json + historical_sites.json
  ──▶ parse-wikipedia-pois.py ──▶ wp_pois
  ──▶ ingest-wikipedia-pois.py ──▶ travel_museums / travel_historical_sites
  ──▶ ingest-neo4j-places.py   ──▶ Neo4j Place + IN_CITY
```

Guide chunks: Wikipedia HTML → `chunk_by_title`. Wipe is the default; use `--no-wipe` to add pages.

**Wikipedia / data license:** Source lists and POI metadata under `data/wiki/` are based on
[Wikipedia](https://www.wikipedia.org/) content. Wikipedia text is available under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (and compatible earlier
versions); reuse requires attribution and share-alike. Application code in this repository
is separately covered by [`LICENSE`](./LICENSE) (All rights reserved) — the Wikipedia
content license does not change the code license.

### Query (online)

1. Guard → planner JSON (`needs_graph` / `graph_queries` / deep / vector)  
2. Optional Neo4j templates (candidates + constraints)  
3. Optional museum/historical Qdrant  
4. Wikipedia guide Qdrant (`travel_chunks`)  
5. Synthesize: source dedupe, `[n]` citations  

Details: `LANGGRAPH.md`, `neo4j.md`.

---

## Tech stack

| Layer | Choice |
|-------|--------|
| LLM | `gemini` (default) or `qwen` (Ollama) |
| Embed | `bge-m3` @ Ollama |
| Vector | Qdrant |
| Graph | Neo4j Community 5.x |
| Orchestration | LangGraph |
| API | FastAPI |
| Ingest | `scripts/` + `scripts/lib/` (PostgreSQL → Qdrant/Neo4j) |
| Frontend | Next.js 14+, React, Tailwind, `react-markdown` + `remark-gfm` |
| Infra | Docker Compose |

---

## Project layout

```
travel-rag/
├── docker-compose.yml       # ollama, api, qdrant, neo4j, postgres
├── .env.example
├── LANGGRAPH.md             # pipeline reference
├── README.md
│
├── api/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── routes/          # ws.py, health
│       ├── services/        # llm, embeddings, vector_store, graph_store, guard, rate_limit
│       ├── graph/           # state, nodes, edges, builder
│       └── models/          # schemas, planner, …
│
├── web/                     # Next.js chat UI (local npm run dev)
│   └── src/
│       ├── components/chat/
│       ├── hooks/useChatStream.ts
│       └── lib/ws.ts
│
└── scripts/
    ├── parse-wiki-pages.py / ingest-wiki-qdrant.py / ingest-wiki-neo4j.py
    ├── parse-wikipedia-pois.py / ingest-wikipedia-pois.py
    ├── ingest-neo4j-places.py
    ├── init-ollama.sh
    ├── setup-venv.sh
    └── lib/                 # wp_chunks, wp_gov, wp_pages_db, neo4j_ingest, …
```

---

## Docker network

```
Host
  ├── :3000  web (npm run dev — not in compose)
  ├── :8000  api
  ├── :6333  qdrant
  ├── :7474/:7687  neo4j
  ├── :11434 ollama (127.0.0.1)
  └── :5433  postgres

llm_internal network: api ↔ ollama, qdrant, neo4j, postgres
```

---

## Running

### Backend

```bash
cp .env.example .env
mkdir -p ~/.config/rag
# If you use Gemini:
echo 'GEMINI_API_KEY=...' >> ~/.config/rag/.env

docker compose up -d
./scripts/init-ollama.sh    # bge-m3, guard, optional qwen
```

API: `http://127.0.0.1:8000` (host ports are localhost-only).  
VPS: `docker compose -f docker-compose.prod.yml up -d` — see [`deploy.md`](./deploy.md) §4.  
After code changes: `docker compose up -d --build api`.

### Frontend

```bash
cd web
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
# http://localhost:3000
```

Chat goes over **WS `/ws/chat`**.

### Ingest (examples)

```bash
# venv + access services from the host
./scripts/setup-venv.sh
export POSTGRES_HOST=localhost QDRANT_URL=http://localhost:6333 \
  OLLAMA_BASE_URL=http://localhost:11434 OLLAMA_EMBED_MODEL=bge-m3

# Wikipedia city pages → travel_chunks (parse then embed)
.venv/bin/python scripts/parse-wiki-pages.py --country Turkey --city Istanbul
.venv/bin/python scripts/ingest-wiki-qdrant.py --city Istanbul --dry-run
.venv/bin/python scripts/ingest-wiki-qdrant.py --country Turkey

# Wikipedia → Neo4j (anchors; + Gemini relations)
.venv/bin/python scripts/ingest-wiki-neo4j.py --city Istanbul --anchors-only
LLM_PROVIDER=gemini .venv/bin/python scripts/ingest-wiki-neo4j.py --city Istanbul

# Wikipedia POI → museums / historical collections + Neo4j Place
.venv/bin/python scripts/parse-wikipedia-pois.py --type museum --city Ankara
.venv/bin/python scripts/ingest-wikipedia-pois.py …
.venv/bin/python scripts/ingest-neo4j-places.py --city Istanbul
```

### Retrieval eval

```bash
QDRANT_URL=http://localhost:6333 OLLAMA_BASE_URL=http://localhost:11434 \
  .venv/bin/python eval/run_retrieval_eval.py
```

Details: [`deploy.md`](./deploy.md) §10.

---

## Environment variables

Two files:

1. **Project `.env`** (from `.env.example`) — provider, models, URLs  
2. **Secrets** `~/.config/rag/.env` — `GEMINI_API_KEY` (do not commit)

Important keys (summary):

```env
LLM_PROVIDER=gemini          # or qwen
GEMINI_MODEL=gemini-2.5-flash-lite
SECRETS_ENV_PATH=~/.config/rag/.env

QWEN_LLM=1b                  # 1b | 8b
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_EMBED_MODEL=bge-m3
OLLAMA_GUARD_MODEL=sileader/qwen3guard:0.6b

QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=travel_chunks
QDRANT_COLLECTION_MUSEUMS=travel_museums
QDRANT_COLLECTION_HISTORICAL=travel_historical_sites

NEO4J_URI=bolt://neo4j:7687
# + PostgreSQL, API, RATE_LIMIT_*  → .env.example
```

---

## API / client

| Endpoint | Role |
|----------|------|
| `WS /ws/chat` | Chat (plan, step, done, sources) |
| `GET /health/*` | Liveness |

`done` payload summary: `answer`, `sources`, `plan`, intent / search flags, hotel/flight query URLs.

Frontend: markdown answers, `[n]` → source links, References list.

---

## Current status (honest summary)

**In place**

- Guard + planner + deep/guide retrieval + synthesize  
- Neo4j v1: schema + ingest + `needs_graph` / template Cypher (`neo4j.md`)  
- City filter, source dedupe, `[n]` citations + FE References  
- Wikipedia city/guide ingest + POI → Qdrant + Place nodes  
- Rate limit, Docker Compose stack  

**Next / weak spots**

- Graph candidates → Qdrant name/QID enrich (`neo4j.md` Runtime §4; ROADMAP **2.A**) — graph is currently constraint/text context only  
- No rerank, groundedness loop, or multi-turn memory yet  
- Web is not a compose service yet  

LangGraph → **`LANGGRAPH.md`**. Neo4j schema (source of truth) → **`neo4j.md`**.
