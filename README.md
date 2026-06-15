# Tourism GraphRAG

A multi-step Retrieval-Augmented Generation system for tourism queries.  
Combines **vector search** (Qdrant) with a **knowledge graph** (Neo4j), orchestrated by **LangGraph** and powered by **Qwen** on Ollama.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                             │
│                                                                     │
│   Jinja2 Templates + jQuery    Postman / curl                       │
│   GET / ──▶ HTML (chat UI)     POST /chat (JSON)                    │
│   WS /ws/chat ──┐              POST /chat ──┐                      │
│                 ▼                            ▼                      │
│            ┌────────────────────────────────────┐                   │
│            │    asyncio.Semaphore(1) Queue      │  ← max 1 LLM call│
│            └────────────────────────────────────┘                   │
└─────────────────────────────┬───────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Orchestration (LangGraph)                           │
│                                                                     │
│   ┌──────────┐    ┌───────────────┐    ┌──────────────────┐        │
│   │  Query   │───▶│ Sub-Question  │───▶│ Parallel         │        │
│   │ Analysis │    │  Generation   │    │ Retrieval        │        │
│   └──────────┘    └───────────────┘    └────────┬─────────┘        │
│        │                                        │                   │
│        │ (simple)                    ┌──────────┴──────────┐        │
│        ▼                             ▼                     ▼        │
│   ┌──────────┐              ┌──────────────┐     ┌─────────────┐   │
│   │  Direct  │              │   Vector     │     │    Graph    │   │
│   │ Retrieve │              │  Retrieval   │     │  Retrieval  │   │
│   └────┬─────┘              └──────┬───────┘     └──────┬──────┘   │
│        │                           └────────┬───────────┘           │
│        ▼                                    ▼                       │
│   ┌─────────────────────────────────────────────────┐               │
│   │        Rerank → Synthesize → Validate           │               │
│   │                    │                            │               │
│   │                    ▼ (not satisfied?)            │               │
│   │              loop back to Sub-Question Gen      │               │
│   └─────────────────────────────────────────────────┘               │
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
This fails for complex tourism questions like:

> "Ailece, bütçe dostu, Akdeniz ikliminde, tarih ve deniz birlikte olan bir yer öner"

This question has multiple constraints (family, budget, climate, history, sea). A single retrieval pass won't capture all dimensions. LangGraph lets us:

1. **Decompose** the query into sub-questions
2. **Route** each sub-question to the right retrieval source (vector vs graph)
3. **Validate** the answer and loop back if insufficient
4. **Stream** intermediate steps to the user in real-time

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
| Frontend | Jinja2 + jQuery + Tailwind (CDN) | — | Server-rendered by FastAPI, no separate build step |
| Infra | Docker Compose | v2 | Multi-service local dev |

---

## Project Structure

```
tourism/
├── docker-compose.yml          # All services (Ollama, API, Qdrant, Neo4j)
├── .env.example                # Environment template
├── README.md                   # This file
│
├── api/                        # Python - FastAPI Backend + Frontend
│   ├── Dockerfile
│   ├── pyproject.toml          # Dependencies (uv/pip)
│   ├── app/
│   │   ├── main.py             # App factory, lifespan, static/template mount
│   │   ├── config.py           # Pydantic Settings (env-based)
│   │   ├── routes/
│   │   │   ├── chat.py         # POST /chat + WS /ws/chat
│   │   │   ├── pages.py        # GET / (serves chat UI template)
│   │   │   └── health.py       # GET /health (DB connectivity)
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
│   ├── templates/              # Jinja2 HTML templates
│   │   ├── base.html           # Layout (head, scripts, CDN imports)
│   │   └── chat.html           # Chat UI page
│   ├── static/                 # CSS, JS assets
│   │   ├── css/
│   │   │   └── app.css
│   │   └── js/
│   │       └── chat.js         # WebSocket + jQuery chat logic
│   └── tests/
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
    ├── seed_data.py            # Insert test tourism chunks
    └── reset_db.sh             # Wipe Qdrant + Neo4j data
```

---

## Docker Network Topology

```
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

All services are defined in the project's `docker-compose.yml`.  
Ollama has no exposed port — only reachable within `llm_internal` network.

---

## Implementation Phases

### Phase 1: Core Pipeline
- [ ] Docker Compose (api + qdrant + neo4j joining llm_internal)
- [ ] FastAPI app skeleton with health checks
- [ ] LLM service with Semaphore(1) concurrency control
- [ ] Embedding service (nomic-embed-text via Ollama)
- [ ] Qdrant vector store service
- [ ] Basic LangGraph pipeline (query → retrieve → synthesize)
- [ ] POST /chat endpoint (testable via Postman)
- [ ] Seed data script (manually insert a few tourism chunks for testing)

### Phase 2: Knowledge Graph
- [ ] Neo4j schema + constraints
- [ ] Graph store service (Cypher execution)
- [ ] GraphRetrieval LangGraph node (NL → Cypher via Qwen)
- [ ] Hybrid merge + reranking logic
- [ ] Extend seed data with entity relationships

### Phase 3: Data Ingestion (Python)
- [ ] Ingestion module scaffold
- [ ] Wikivoyage MediaWiki API client
- [ ] Wikidata SPARQL client
- [ ] Section-based chunker
- [ ] Bulk loader (Qdrant + Neo4j)
- [ ] CLI entrypoint with config-driven city list

### Phase 4: Frontend
- [ ] Jinja2 base template + Tailwind CDN setup
- [ ] Chat page template (chat.html)
- [ ] jQuery + WebSocket chat logic (static/js/chat.js)
- [ ] Thinking steps display (shows LangGraph progress)
- [ ] Queue position indicator

### Phase 5: Guardrails & Safety
- [ ] GuardrailService interface (`check_input` → allow/deny)
- [ ] Rule-based pre-filter (empty, too long, keyword blocklist)
- [ ] Türkçe profanity keyword list
- [ ] Llama Prompt Guard 2-86M (prompt injection / jailbreak detection)
- [ ] Domain classifier (turizm sorusu mu? — Qwen mini-prompt veya mDeBERTa zero-shot)
- [ ] Output safety check (hallucination / toxic response filtering)

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
| Query decomposition | LangGraph state machine | Supports loops, branching, streaming; more than a simple chain |
| Embedding location | Same Ollama instance | Operational simplicity; nomic-embed-text is lightweight |
| Ingestion language | Python | Shared deps with API, single language across project |
| Frontend approach | Jinja2 + jQuery (server-rendered) | No separate build/container, FastAPI serves everything |
| WebSocket | Yes | Enables streaming + step visualization; REST stays for simple clients |
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
QDRANT_COLLECTION=tourism_chunks

# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme

# API
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=info
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
