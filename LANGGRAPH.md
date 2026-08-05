# Travel RAG — LangGraph Design

Bu dosya **şu an çalışan** LangGraph pipeline’ını tanımlar. Kod: `api/app/graph/` (`state.py`, `nodes.py`, `edges.py`, `builder.py`).

Neo4j şema + template’ler: **[`neo4j.md`](./neo4j.md)** (graph = aday/kısıt; Qdrant = anlatım).

---

## Graph (mevcut)

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

| Node | LLM? | Rol |
|------|------|-----|
| `intake` | Hayır | Soruyu normalize eder |
| `safety_guard` | Guard model (Ollama) | Prompt injection / unsafe input |
| `planner_json` | **Evet (#1)** | Intent, plan, arama + opsiyonel `graph_queries` |
| `chitchat` | **Evet** | Retrieval’sız kısa cevap |
| `graph_retrieval` | Hayır | Neo4j allowlist template Cypher (`needs_graph`) |
| `deep_retrieval` | Hayır (embed) | Müze + tarihi alan Qdrant koleksiyonları |
| `vector_retrieval` | Hayır (embed) | Wikivoyage guide chunk’ları |
| `synthesize` | **Evet (#2)** | Kaynaklara dayalı cevap + citation |
| `format` | Hayır | Pass-through (ileride post-process) |

**LLM çağrıları (tipik travel sorusu):** planner + synthesize (= 2). Chitchat’te 1. Guard ayrı küçük model.

---

## LLM & embedding

```
planner_json / chitchat / synthesize ──▶ llm.generate(prompt)
                                              ├─ gemini → Gemini API (default: gemini-2.5-flash-lite)
                                              └─ qwen   → Ollama (qwen3:1.7b veya qwen3:8b)

embeddings (her retrieval) ──▶ Ollama bge-m3
safety_guard               ──▶ Ollama sileader/qwen3guard:0.6b
```

- **Seçim:** `.env` → `LLM_PROVIDER=gemini|qwen`
- **Secrets:** `~/.config/rag/.env` → `GEMINI_API_KEY` (`SECRETS_ENV_PATH`)
- **Ollama generation:** `asyncio.Semaphore(1)` (yerel GPU sırası). Gemini HTTP API; semaphore yalnızca Ollama yolunda.

---

## State (`GraphState`)

```
question                 str
history                  list[{role, content}]
primary_intent           str   # chitchat|factoid|recommendation|planning|comparison|logistics|meta
intents                  list[{intent, cities}]
prompt_language          str   # ISO 639-1
plan                     {summary, steps[]}
search_queries           list[{q, city?}]      # guide / Wikivoyage
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
museum_chunks            list[dict]
artifact_chunks          list[dict]
vector_chunks            list[dict]
graph_contexts           list[{city, name?, text, source_url?, source?}]  # neo4j template sonuçları
sources                  list[dict]            # synthesize sonrası, [1]…[n] sırası
answer                   str
error                    str | None
guard_label / guard_raw  …
planner_raw              str                   # debug
```

---

## Routing

`retrieval_router` (`edges.py`):

| Koşul | Hedef |
|--------|--------|
| `error` set | `format` |
| `primary_intent ∈ {chitchat, meta}` | `chitchat` |
| `needs_graph == true` | `graph_retrieval` → sonra `deep` veya `vector` |
| `deep_search == true` | `deep_retrieval` → `vector_retrieval` |
| aksi | `vector_retrieval` |

`deep_search` yalnızca müze / tarihi Qdrant aramasını açar. Neo4j yalnızca `needs_graph` + allowlist `graph_queries` ile çalışır (LLM Cypher yok).

---

## `planner_json` — tek LLM, çoklu çıktı

Ayrı intent / entity / rewrite node’ları yok. Tek çağrı JSON döner:

```jsonc
{
  "primary_intent": "planning",
  "intents": [
    { "intent": "planning", "cities": ["Antalya"] }
  ],
  "prompt_language": "tr",
  "plan": {
    "summary": "Antalya için 3 günlük müze odaklı bir plan çıkaracağım.",
    "steps": [
      { "id": "s1", "label": "Müzeleri ara", "kind": "museums", "status": "idle" },
      { "id": "s2", "label": "Rehber metinlerini ara", "kind": "vector", "status": "idle" },
      { "id": "s3", "label": "Cevabı yaz", "kind": "synthesize", "status": "idle" }
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

### Arama sorguları

- Her sorgu `{ "q": "...", "city": "CityName" | "ANY" }`.
- `city` bilinen şehirde Qdrant `city_name` filtresi; `ANY` / omit → filtresiz.
- Guide sorguları İngilizce, kısa, retrieval-optimized.
- Rota / “ne görülür” / müze-tarih sorularında planner `deep_search` + ilgili POI sorgularını açmaya yönlendirilir.

### Graph sorguları (`neo4j.md`)

- Şema + ilişki + template listesi: **[`neo4j.md`](./neo4j.md)** (tek referans).
- `needs_graph` + `graph_queries: [{ template, params }]`.
- Template allowlist: `places_in_city`, `day_trips_from_city`, `connection_between_cities`, `airports_serving_city`.
- LLM Cypher yazmaz; kod `graph_store.GRAPH_TEMPLATES` çalıştırır.
- **Hedef hibrit** (`neo4j.md` Runtime §4): graph aday → Qdrant name/QID enrich. **Şu an yok** — graph metin bağlamı + ayrı vector sorguları; köprü için ROADMAP **2.A**.

### Plan `steps[].kind`

`vector | museums | artifacts | graph | external | synthesize`  
Son adım her zaman `synthesize`. Asıl routing: `needs_graph` / `deep_search` / `primary_intent`.

---

## Retrieval

### `graph_retrieval_node`

- Yalnızca `needs_graph` ve dolu `graph_queries` iken.
- Allowlist template Cypher → satırları metne çevirir → `graph_contexts` (`source_url` edge/node’dan; citation için).
- Graph adaylarıyla Qdrant’ı filtrelemez (bkz. yukarı — `neo4j.md` §4 / 2.A).
- Sonra `after_graph_router`: deep varsa `deep_retrieval`, değilse `vector_retrieval`.

### `deep_retrieval_node`

- Koleksiyonlar: `travel_museums`, `travel_historical_sites` (env).
- `museum_search_queries` / `artifact_search_queries` + city filter.
- Sonuçlar: `museum_chunks`, `artifact_chunks`.

### `vector_retrieval_node`

- Koleksiyon: `travel_chunks` (Wikivoyage guide).
- `search_queries` + city filter.
- Sonuç: `vector_chunks`.

---

## `synthesize_node`

1. Kaynakları birleştirir: **museum → historical → guide → graph**.
2. Aynı `source_url` + aynı `section` → tek kaynak (metin birleşir).
3. Numaralandırma: `[1]`, `[2]`, … (artık `[S1]` yok).
4. Intent’e göre ek talimat (factoid / recommendation / planning / comparison / logistics). Comparison → markdown tablo (≤5 satır).
5. Grounding: yalnızca verilen kaynaklar; kısmi cevap politikası.
6. Hotel/flight URL’leri varsa prompt’a harici link bölümü eklenir.
7. Çıktı: `answer` + `sources` (citation sırasıyla aynı liste).

FE: metindeki `[n]` → `sources[n-1].source_url` linki; altta Chicago tarzı **References**.

---

## WebSocket (`WS /ws/chat`)

**Single-shot:** bir soru = bir bağlantı; `done` / `error` sonrası server kapatır.

### Client → server

```jsonc
{ "type": "message", "content": "…", "history": [] }
```

### Server → client

| Event | Ne zaman |
|--------|----------|
| `status` | Bağlantı sonrası hemen |
| `plan` | `planner_json` sonrası (`summary`, `steps`) |
| `step` | Retrieval / synthesize ilerleme (`kind`, `status`, `detail`) |
| `done` | Cevap + `sources` + planner alanları; WS kapanır |
| `error` | Guard / planner / runtime; WS kapanır |

Token-by-token stream şu an `astream(updates)` üzerinden node güncellemeleri ile ilerler; UI plan + step + final answer gösterir. Markdown (GFM) `MessageBubble` içinde render edilir.

Rate limit: in-memory per IP (`RATE_LIMIT_*` env).

---

## Bilinçli olarak henüz yok

- Ayrı intent / entity / slot / clarify node’ları
- Rerank / cross-encoder
- Groundedness / completeness loop
- LLM-generated Cypher
- Multi-turn bellek / anaphora resolver

Bunlar hedef genişletmeler; mevcut ürün yukarıdaki graph’tır.

---

## Tasarım notları

- **2 LLM** (planner + synthesize) — maliyet/gecikme dengesi.
- **Plan UX** boş ekranı keser; routing flag’lerden gelir.
- **City-aware vector filter** gürültüyü azaltır.
- **Section allowlist** (ingest) sleep/eat vb. ticari gürültüyü keser — bkz. README ingest.
- **Citation index = sources list index** — FE remap’siz.
- Node’lar: state in → partial state out; yan etki = dış servis + WS emit.
