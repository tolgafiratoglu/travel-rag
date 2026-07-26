# Travel RAG

Seyahat soruları için çok adımlı Retrieval-Augmented Generation sistemi.  
**LangGraph** ile orkestrasyon, **Qdrant** ile vektör arama, **Neo4j** ile hafif şehir profili (özet), **Next.js** sohbet arayüzü.

| Rol | Teknoloji |
|-----|-----------|
| Generation (default) | Gemini `gemini-2.5-flash-lite` |
| Generation (local) | Qwen via Ollama (`LLM_PROVIDER=qwen`) |
| Embeddings | Ollama **`bge-m3`** |
| Safety guard | Ollama `sileader/qwen3guard:0.6b` |
| Vector DB | Qdrant (`travel_chunks`, `travel_museums`, `travel_historical_sites`) |
| Graph DB | Neo4j 5 (şehir profili sidecar — detay bu README’de yok) |
| API | FastAPI + WebSocket |
| UI | Next.js App Router + React + Tailwind |

Pipeline tasarımı: **[`LANGGRAPH.md`](./LANGGRAPH.md)**.

---

## Mimari (özet)

```
Browser (Next.js :3000)
    │  WS /ws/chat  (tek soru = tek bağlantı)
    ▼
FastAPI (:8000)
    │
    ▼
LangGraph
  intake → safety_guard → planner_json
              │
              ├─ chitchat/meta → chitchat → format
              ├─ deep_search → museums/historical → Wikivoyage → Neo4j profile → synthesize
              └─ else → Wikivoyage → Neo4j profile → synthesize
    │
    ├── Qdrant (guide + optional POI collections)
    ├── Neo4j (city profile text)
    ├── Gemini veya Ollama/Qwen (generation)
    └── Ollama (embeddings + guard)
```

İki aşamalı UX: önce plan özeti (WS `plan`), sonra retrieval adımları (`step`), sonra kaynaklı cevap (`done`). Citation formatı `[1]`, `[2]`, …; FE kaynakçayı Chicago tarzında listeler.

---

## Neden bu şekil?

- **LangGraph** — tek zincir yerine plan → route → retrieve → synthesize; chitchat ve deep POI ayrı path.
- **Plan-first WS** — kullanıcı boş ekranda beklemez; sistem ne yapacağını erken gösterir.
- **City-aware Qdrant filter** — planner `SearchQuery { q, city }` ile şehir metadata’sına pinler (`ANY` = filtresiz).
- **Deep search** — müze / tarihi alan koleksiyonları; `deep_search` bunları açar (Neo4j’i değil).
- **Section allowlist (ingest)** — See / Do / Get in vb.; Sleep / Eat gibi ticari gürültü yok.
- **Ollama Semaphore(1)** — yalnızca local Qwen generation için GPU sırası; Gemini HTTP.

---

## Veri akışı

### Ingest (offline)

```
Wikivoyage dump / API ──▶ MariaDB (wv_cities)
                              │
                              ├─▶ scripts/ingest-wikivoyage-qdrant.py  ──▶ Qdrant travel_chunks
                              └─▶ scripts/ingest-wikivoyage-neo4j.py   ──▶ Neo4j (özet)

Wikipedia POI rows   ──▶ scripts/ingest-wikipedia-pois.py
                              ├─▶ travel_museums
                              └─▶ travel_historical_sites
```

**Wikivoyage section politikası** (`scripts/lib/wv_parse.py`):

- Tam: `see`, `do`, `get_in`, `districts`, `go_next`
- `understand`: yalnızca history / climate
- `get_around`: toplu taşıma / yürüme / bisiklet (+ ücret); taxi/car red
- Intro/lead ve sleep/eat/buy vb. **yok**

Qdrant’a şehir eklerken: ilk yüklemede wipe varsayılan; ek şehir için `--no-wipe`.

### Query (online)

1. Guard → planner JSON  
2. Gerekirse müze/tarihi arama  
3. Wikivoyage guide arama (şehir filtresi)  
4. Neo4j şehir profili (varsa)  
5. Synthesize: kaynak dedupe (`url` + `section`), `[n]` citation, intent odaklı prompt  

Detay: `LANGGRAPH.md`.

---

## Tech stack

| Katman | Seçim |
|--------|--------|
| LLM | `gemini` (default) veya `qwen` (Ollama) |
| Embed | `bge-m3` @ Ollama |
| Vector | Qdrant |
| Graph | Neo4j Community 5.x |
| Orchestration | LangGraph |
| API | FastAPI |
| Ingest | `scripts/` + `scripts/lib/` (MariaDB → Qdrant/Neo4j) |
| Frontend | Next.js 14+, React, Tailwind, `react-markdown` + `remark-gfm` |
| Infra | Docker Compose |

---

## Proje yapısı

```
travel-rag/
├── docker-compose.yml       # ollama, api, qdrant, neo4j, mariadb
├── .env.example
├── LANGGRAPH.md             # pipeline referansı
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
├── web/                     # Next.js chat UI (lokal npm run dev)
│   └── src/
│       ├── components/chat/
│       ├── hooks/useChatStream.ts
│       └── lib/ws.ts
│
└── scripts/
    ├── ingest-wikivoyage-qdrant.py
    ├── ingest-wikipedia-pois.py
    ├── ingest-wikivoyage-neo4j.py
    ├── parse-wikivoyage.py / parse-wikipedia-pois.py
    ├── import-wikivoyage.sh
    ├── init-ollama.sh
    ├── setup-venv.sh
    └── lib/                 # wv_parse, wv_chunks, wp_*, neo4j_wv_ingest, …
```

---

## Docker ağı

```
Host
  ├── :3000  web (npm run dev — compose’da değil)
  ├── :8000  api
  ├── :6333  qdrant
  ├── :7474/:7687  neo4j
  ├── :11434 ollama (127.0.0.1)
  └── :3306  mariadb

llm_internal network: api ↔ ollama, qdrant, neo4j, mariadb
```

---

## Çalıştırma

### Backend

```bash
cp .env.example .env
mkdir -p ~/.config/rag
# Gemini kullanacaksan:
echo 'GEMINI_API_KEY=...' >> ~/.config/rag/.env

docker compose up -d
./scripts/init-ollama.sh    # bge-m3, guard, isteğe bağlı qwen
```

API: `http://localhost:8000` — health endpoint’leri mevcut.  
Kod değişince: `docker compose up -d --build api`.

### Frontend

```bash
cd web
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
# http://localhost:3000
```

Sohbet **WS `/ws/chat`** üzerinden gider.

### Ingest (örnek)

```bash
# venv + host’tan servislere erişim
./scripts/setup-venv.sh
export MARIADB_HOST=localhost QDRANT_URL=http://localhost:6333 \
  OLLAMA_BASE_URL=http://localhost:11434 OLLAMA_EMBED_MODEL=bge-m3

# Wikivoyage → Qdrant (ilk şehir wipe eder; sonrakiler --no-wipe)
.venv/bin/python scripts/ingest-wikivoyage-qdrant.py --page Antalya
.venv/bin/python scripts/ingest-wikivoyage-qdrant.py --page Istanbul --no-wipe

# Wikipedia POI → museums / historical collections
.venv/bin/python scripts/ingest-wikipedia-pois.py …
```

---

## Ortam değişkenleri

İki dosya:

1. **Proje `.env`** (`.env.example`’dan) — provider, model, URL’ler  
2. **Secrets** `~/.config/rag/.env` — `GEMINI_API_KEY` (commit etme)

Önemli anahtarlar (özet):

```env
LLM_PROVIDER=gemini          # veya qwen
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
# + MariaDB, API, RATE_LIMIT_*  → .env.example
```

---

## API / istemci

| Endpoint | Rol |
|----------|-----|
| `WS /ws/chat` | Sohbet (plan, step, done, sources) |
| `GET /health/*` | Canlılık |

`done` payload özeti: `answer`, `sources`, `plan`, intent / search flag’leri, hotel/flight query URL’leri.

FE: markdown cevap, `[n]` → kaynak linki, References listesi.

---

## Mevcut durum (dürüst özet)

**Var**

- Guard + planner + deep/guide retrieval + synthesize  
- City filter, kaynak dedupe, `[n]` citation + FE References  
- Wikivoyage allowlist ingest, Wikipedia POI collections  
- Rate limit, Docker Compose stack  

**Bilerek sade / sonraki iş**

- Neo4j: city-profile sidecar (çok-hop / label-routed Cypher yok)  
- Rerank, groundedness loop, multi-turn bellek yok  
- Web henüz compose servisi değil  

LangGraph node/edge detayı → **`LANGGRAPH.md`**.
