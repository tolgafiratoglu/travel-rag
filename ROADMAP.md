# Travel RAG — Roadmap

Türkçe ve çok dilli seyahat sorularını cevaplayan, lokal çalışan, hibrit (vector + knowledge graph) bir GraphRAG sistemi.
Stack: **Qwen3:8b (Ollama)** + **LangGraph** + **FastAPI** + **Qdrant** + **Neo4j** + **Next.js (React, App Router)**.

Süre tahminleri günlük 2-3 saat yarı zamanlı çalışmaya göre verilmiştir.

---

## Mevcut durum (özet)

- [x] Docker altyapısı (Ollama, API, Qdrant, Neo4j)
- [x] FastAPI iskelet + `GET /health`
- [x] `llm.py` (Semaphore + keep_alive + think=false), `embeddings.py`, `vector_store.py`
- [x] `POST /chat` — düz RAG akışı
- [x] `seed.py` + `init-ollama.sh`
- [x] Warmup, model rename (Travel RAG)
- [x] `web/` — Next.js chat UI (TS + Tailwind), iki state'li landing/conversation
- [x] FastAPI CORS (`http://localhost:3000`)

> **Not — `/chat` endpoint'i:** Şu an düz LLM çağrısı yapan **test/Postman amaçlı** bir endpoint. Production transport **WebSocket** olacak (Phase 1 + 3.D). `/chat` Phase 1 sonunda `deprecated` işaretlenecek, Phase 5 sonunda kaldırılacak.

---

## Phase 0 — RAG Hardening  *(~2 gün)*

Mevcut `/chat` akışını gözlemlenebilir ve hata toleranslı hale getir.

- [ ] **0.1** `schemas.py` — `ChatResponse`'a `latency_ms`, `retrieved_count`, `model` alanları *(0.5 gün)*
- [ ] **0.2** `llm.py` — Custom exception'lar (`LLMTimeoutError`, `LLMUpstreamError`) *(0.5 gün)*
- [ ] **0.3** `vector_store.py` — `top_k` clamp (1..10), `min_score` filtresi, payload safe defaults *(0.5 gün)*
- [ ] **0.4** `chat.py` — Empty input validation, empty retrieval handling, exception → HTTP status mapping (400/502/504) *(0.5 gün)*
- [ ] **0.5** `chat.py` — Prompt'ta kaynak numaralandırma (`[S1] [S2]`), "uydurma yok" talimatı *(0.5 gün)*
- [ ] **0.6** Request logging middleware (request_id, query length, retrieval count, latency) *(0.5 gün)*
- [ ] **0.7** `health.py` → `/health/live` + `/health/ready` (Qdrant + Ollama check) *(0.5 gün)*

**Çıktı:** Her senaryoda doğru status code, ölçülebilir latency, kaynaklı cevaplar.

---

## Phase 1 — Çalışan demo (5 node + WebSocket)  *(~4 gün)*

İlk canlı sürüm: **5 node, 2 LLM çağrısı, Qdrant-only, WS primary transport.** Ayrı `intent`/`entity`/`plan`/`rewrite` node'ları yok — hepsi `planner_json_node` içinde.

> Detay: `LANGGRAPH.md` → "Phase 1 — Minimal graph".

### 1.A Graph iskeleti  *(~2 gün)*

- [ ] **1.1** `graph/state.py` — TypedDict (Phase 1 alanları: `question`, `history`, `intent`, `entities`, `assumptions`, `plan`, `search_queries`, `vector_chunks`, `sources`, `answer`, `latency_ms`) *(0.5 gün)*
- [ ] **1.2** `graph/nodes.py` — `intake_node` (normalize, history; LLM yok) *(0.25 gün)*
- [ ] **1.3** `graph/nodes.py` — `planner_json_node` (tek LLM: intent + entities + assumptions + plan + search_queries JSON) *(0.75 gün)*
- [ ] **1.4** `graph/nodes.py` — `vector_retrieval_node` (search_queries → Qdrant, dedup) *(0.5 gün)*
- [ ] **1.5** `graph/nodes.py` — `synthesize_node` + `format_node` *(0.5 gün)*
- [ ] **1.6** `graph/builder.py` — linear graph: `intake → planner_json → vector_retrieval → synthesize → format` *(0.25 gün)*

### 1.B WebSocket + streaming  *(~1.5 gün)*

- [ ] **1.7** `WS /ws/chat` — single-shot; events: `status`, `plan`, `step`, `token`, `done`, `error`; server `done`/`error` sonrası kapatır *(0.5 gün)*
- [ ] **1.8** `status` event — WS açılır açılmaz ("Sorunu analiz ediyorum…"); `plan` planner bitince *(0.25 gün)*
- [ ] **1.9** `services/llm.py` — `stream=True` (synthesize token'ları) *(0.5 gün)*
- [ ] **1.10** LangGraph `astream_events()` adaptörü — node → WS event mapping *(0.25 gün)*

### 1.C RAG kalite + test endpoint  *(~0.5 gün)*

- [ ] **1.11** Prompt: `[S1]` citation, "uydurma yok"; boş retrieval handling *(0.25 gün)*
- [ ] **1.12** `POST /chat` — graph üzerinden test; **`deprecated` notu** (Phase 5'te kaldırılacak) *(0.25 gün)*

**Çıktı:** Tarayıcıdan veya WS client'tan soru → status → plan → step → token stream → done. Neo4j, rerank, router, self-reflection **yok**.

> Ertelenen (Phase 4+): `intent_classifier`, `decomposition`, `query_rewrite`, `retrieval_router`, `chitchat` patikası, `clarify` hard ask.

---

## Phase 2 — Neo4j (template Cypher)  *(~4 gün)*

Neo4j online sorguya dahil — önce **hazır Cypher template'leri**, LLM Cypher generation sonra.

### 2.A Graph service  *(~1 gün)*

- [ ] **2.1** `services/graph_store.py` — Neo4j async driver wrapper *(0.5 gün)*
- [ ] **2.2** Helper'lar: `cities_near`, `unesco_near_city`, `airport_access` + generic `run_cypher` *(0.5 gün)*

### 2.B Template Cypher (LLM yok)  *(~1.5 gün)*

- [ ] **2.3** `graph/nodes.py` — `graph_retrieval_node` intent'e göre hazır template seçer *(0.75 gün)*
- [ ] **2.4** Cypher validation guard (sadece MATCH/RETURN) *(0.25 gün)*
- [ ] **2.5** `retrieval_router` edge — simple → vector-only, structured → hybrid *(0.5 gün)*

### 2.C Hybrid merge  *(~1.5 gün)*

- [ ] **2.6** `merge_dedupe_node` (vector + graph birleştir) *(0.5 gün)*
- [ ] **2.7** SourceChunk köprüsünden vector chunk'ları getir *(0.5 gün)*
- [ ] **2.8** `planner_json` plan steps'e `graph` kind ekle (UNESCO/mesafe soruları) *(0.5 gün)*

**Çıktı:** "İstanbul'a yakın UNESCO yerleri", "Kaş Fethiye'ye mi yakın" gibi yapılandırılmış sorular.

> Ertelenen: LLM Cypher generation (`generate_cypher_node`) → Phase 4; `rerank_node` → Phase 4.

### 2.D LLM Cypher (ileri)  *(~2 gün — Phase 4'e taşınabilir)*

- [ ] **2.9** `generate_cypher_node` (Qwen + few-shot) *(1 gün)*
- [ ] **2.10** Template fallback: LLM Cypher başarısızsa hazır template'e düş *(0.5 gün)*

---

## Phase 3 — Frontend (Next.js / React)  *(~7 gün kaldı)*

Tek sayfa RAG chat: kullanıcı soru sorar, WebSocket üstünden plan + adım adım ilerleme + token stream alır. Şehir sayfaları ve SEO bu fazda yok.

### 3.A Scaffold  *(~0.5 gün — tamamlandı)*

- [x] **3.1** `web/` — Next.js 14 (TS + Tailwind + App Router) *(0.5 gün)*
- [x] **3.2** `next.config.mjs`, `.env.local.example` (`NEXT_PUBLIC_API_BASE_URL`) *(0.25 gün)*
- [x] **3.5** FastAPI CORS middleware (`http://localhost:3000`) *(0.25 gün)*

### 3.B Chat UI  *(~2 gün — tamamlandı)*

- [x] **3.6** `src/app/layout.tsx` — Server Component, metadata, `h-dvh` *(0.25 gün)*
- [x] **3.7** `src/app/page.tsx` — Server Component, `<ChatApp />` *(0.25 gün)*
- [x] **3.8** `src/lib/api.ts` — `postChat()` (test/fallback için) *(0.25 gün)*
- [x] **3.9** `ChatApp.tsx` — iki state'li (landing / conversation) *(0.5 gün)*
- [x] **3.10** `LandingScreen`, `ConversationScreen`, `MessageList`, `MessageBubble`, `ChatInput`, `SuggestionChips` *(0.5 gün)*
- [x] **3.13** Tailwind ile temel temalama, ChatGPT tarzı input *(0.25 gün)*

### 3.C Kalan UI iyileştirmeleri  *(~1 gün)*

- [ ] **3.12** Boş/hata/uzun bekleme durumları için daha iyi UX *(0.25 gün)*
- [ ] **3.14** `web/Dockerfile` + `docker-compose.yml` web servisi *(0.5 gün)*
- [ ] **3.15** (Multi-turn yapıldıktan sonra) chat history persist *(0.25 gün)*

### 3.D WebSocket streaming — primary transport  *(~3.5 gün)*

> Detaylı event tablosu ve UI davranışı için `LANGGRAPH.md` → "Chain-of-Thought UX".

#### 3.D.1 Sunucu  *(Phase 1.B ile örtüşür — FE bağlanınca tamamlanmış olmalı)*

- [ ] **3.16** `status` event UI'da göster (Phase 1.B'de sunucu tarafı) *(0.25 gün)*
- [ ] **3.17** LangGraph event adaptörü FE ile entegre test *(0.25 gün)*

#### 3.D.2 İstemci  *(~2 gün)*

- [ ] **3.19** `web/src/lib/ws.ts` + `hooks/useChatStream.ts` — single-shot WS; event router (`status` / `plan` / `step` / `token` / `done` / `error`) *(0.75 gün)*
- [ ] **3.20** `PlanCard.tsx` — `status` satırı + verbal `summary` + `steps[]` checklist *(0.5 gün)*
- [ ] **3.21** Thinking satırı davranışı — `step:running` ile gri "Thinking…" satırı; `step:done` payload'ı (`label`, `detail`) ile satır değişir *(0.25 gün)*
- [ ] **3.22** Token streaming — `token` event'i asistan balonuna append; ilk token gelince Thinking satırı kaldırılır *(0.25 gün)*
- [ ] **3.23** Hata durumu — son `step:running` satırı kırmızıya döner + asistan balonunda "Üzgünüm, … adımında hata oldu" *(0.25 gün)*

**Çıktı:** Kullanıcı soru sorar; ChatGPT benzeri "Thinking → adım bulgusu → cevap streaming" akışı; WS soru bitince kapanır.

> **Ertelenen:** Şehir sayfaları (`/city/[slug]`), `robots.ts`, `sitemap.ts` — stretch goal **S.7** veya ingestion sonrası.

---

## Phase 4 — Data Ingestion (Wikivoyage + Wikidata)  *(~7 gün)*

Seed verisini gerçek veriyle değiştir.

### 4.A Ingestion modülü  *(~5 gün)*

- [ ] **4.1** `ingestion/wikivoyage.py` — MediaWiki API client (`get_page_sections`) *(1.5 gün)*
- [ ] **4.2** `ingestion/wikidata.py` — SPARQL client (`get_city_metadata(qid)`) *(1 gün)*
- [ ] **4.3** `ingestion/chunker.py` — Section-based chunking, overlap, metadata preserve *(1 gün)*
- [ ] **4.4** `ingestion/loader.py` — Embed + Qdrant upsert + Neo4j entity/SourceChunk *(1 gün)*
- [ ] **4.5** `ingestion/configs/sources.yaml` — Hedef şehirler listesi *(0.5 gün)*

### 4.B CLI ve orchestration  *(~2 gün)*

- [ ] **4.6** `ingestion/run.py` — CLI (`python -m ingestion.run --config sources.yaml`) *(0.5 gün)*
- [ ] **4.7** Idempotent re-run (chunk_id deterministic, MERGE bazlı) *(0.5 gün)*
- [ ] **4.8** Progress log + error retry *(0.5 gün)*
- [ ] **4.9** İlk 20 şehir için tam pipeline çalıştır *(0.5 gün)*

**Çıktı:** Sistem 20+ şehir için gerçek Wikivoyage verisiyle dolu.

---

## Phase 5 — Guardrails & Safety  *(~4 gün)*

Kötü niyetli / domain dışı sorgulardan koru.

### 5.A Interface ve rule-based  *(~1 gün)*

- [ ] **5.1** `services/guardrails.py` — `GuardrailService.check_input()` interface *(0.25 gün)*
- [ ] **5.2** Rule-based pre-filter (empty, too long, suspicious keywords) *(0.25 gün)*
- [ ] **5.3** Türkçe profanity keyword list *(0.25 gün)*
- [ ] **5.4** `chat.py` ve `ws/chat`'e guardrail entegrasyonu *(0.25 gün)*

### 5.B Model-based guards  *(~2 gün)*

- [ ] **5.5** Llama Prompt Guard 2-86M entegrasyonu *(1 gün)*
- [ ] **5.6** Domain classifier (Qwen mini-prompt: "seyahat sorusu mu?") *(0.5 gün)*
- [ ] **5.7** Domain dışı sorgular için kullanıcı dostu reddetme mesajı *(0.5 gün)*

### 5.C Output safety  *(~1 gün)*

- [ ] **5.8** LLM cevabında hassas bilgi / halüsinasyon temel kontrolü *(0.5 gün)*
- [ ] **5.9** Kaynaksız iddiaları işaretle ("kaynaklara dayanmıyor") *(0.5 gün)*

### 5.D Transport temizliği  *(~0.5 gün)*

- [ ] **5.10** `POST /chat` endpoint'ini kaldır — istemci WebSocket tek transport *(0.25 gün)*
- [ ] **5.11** `web/src/lib/api.ts` (`postChat`) sil; sadece `lib/ws.ts` kalsın *(0.25 gün)*

**Çıktı:** "ignore previous instructions" gibi saldırılar tutulur, alakasız sorular nazikçe reddedilir; REST chat surface tamamen kalkar.

---

## Phase 6 — Observability  *(~3 gün)*

Production'da neler olduğunu görebilmek.

- [ ] **6.1** `structlog` entegrasyonu (JSON loglar) *(0.5 gün)*
- [ ] **6.2** LLM çağrısı metrikleri (model, prompt length, response length, latency) *(0.5 gün)*
- [ ] **6.3** Retrieval metrikleri (query, top_k, max_score, retrieval_time) *(0.25 gün)*
- [ ] **6.4** Semaphore queue depth metric *(0.25 gün)*
- [ ] **6.5** OpenTelemetry tracing (request → graph nodes → LLM/retrieval spans) *(1 gün)*
- [ ] **6.6** (Opsiyonel) Prometheus metrics endpoint *(0.5 gün)*
- [ ] **6.7** Error tracking (Sentry veya log seviyesi) *(0.25 gün)*

**Çıktı:** "Yavaş" diyen kullanıcının hangi adımda kaybedildiği görülür.

---

## Phase 7 — Quality & Evaluation  *(~5 gün)*

Retrieval ve cevap kalitesini ölçülebilir hale getir.

- [ ] **7.1** `tests/eval/golden_qa.yaml` — 30-50 soru/beklenen cevap çifti *(1 gün)*
- [ ] **7.2** `tests/eval/run_eval.py` — Otomatik değerlendirme scripti *(1 gün)*
- [ ] **7.3** Retrieval metrikleri (recall@k, MRR) *(0.5 gün)*
- [ ] **7.4** Cevap kalite metriği (LLM-as-judge, basit puanlama) *(0.5 gün)*
- [ ] **7.5** Embedding modelini karşılaştır (`nomic-embed-text` vs `bge-m3`) *(1 gün)*
- [ ] **7.6** Reranker dene (cross-encoder, opsiyonel) *(0.5 gün)*
- [ ] **7.7** Chunking strategy A/B test (chunk size, overlap) *(0.5 gün)*

**Çıktı:** "Şu değişiklik kaliteyi artırdı mı?" sorusu sayılarla cevaplanır.

---

## Phase 8 — Production Polish  *(~6 gün)*

Demo'dan production'a geçirmek için son adımlar.

### 8.A Reliability  *(~1.5 gün)*

- [ ] **8.1** Tüm servisler için Docker healthcheck *(0.25 gün)*
- [ ] **8.2** API'de graceful shutdown *(0.25 gün)*
- [ ] **8.3** Retry policy (Ollama transient errors) *(0.5 gün)*
- [ ] **8.4** Rate limiting (per-IP) *(0.5 gün)*

### 8.B Security  *(~1.5 gün)*

- [ ] **8.5** API key authentication (`X-API-Key` header) *(0.5 gün)*
- [ ] **8.6** CORS configuration *(0.25 gün)*
- [ ] **8.7** Neo4j password env-based (hardcoded `changeme` çıkar) *(0.25 gün)*
- [ ] **8.8** Secrets management (`.env` git'te değil, `.env.example` ekle) *(0.5 gün)*

### 8.C Documentation  *(~1.5 gün)*

- [ ] **8.9** README "Run locally" / "Deploy to server" bölümleri *(0.5 gün)*
- [ ] **8.10** API docs örneklerle zenginleştir *(0.25 gün)*
- [ ] **8.11** Architecture diagram (Mermaid + rendered PNG) *(0.5 gün)*
- [ ] **8.12** Demo video / GIF *(0.25 gün)*

### 8.D Deployment  *(~1.5 gün)*

- [ ] **8.13** Sunucu setup talimatları (Docker, GPU driver vs) *(0.5 gün)*
- [ ] **8.14** Reverse proxy (nginx/caddy) + TLS *(0.5 gün)*
- [ ] **8.15** Backup stratejisi (Qdrant + Neo4j volume snapshot) *(0.5 gün)*

**Çıktı:** Proje sunucuya deploy edilmiş, dış dünyaya erişilebilir, dokümante.

---

## Stretch Goals  *(her biri ~2-4 gün)*

Zaman kalırsa ya da projeyi öne çıkarmak için:

- [ ] **S.1** Multi-turn conversation (chat history context) *(2 gün)*
- [ ] **S.2** User preference memory (Neo4j'de user node) *(3 gün)*
- [ ] **S.3** Trip planner agent (multi-step planning) *(4 gün)*
- [ ] **S.4** Image support (chunk'lara fotoğraf, frontend'de göster) *(3 gün)*
- [ ] **S.5** Voice input (Whisper) *(2 gün)*
- [ ] **S.6** Multi-language UI (İngilizce + Türkçe toggle) *(2 gün)*
- [ ] **S.7** Public city pages — SSG/SSR `/city/[slug]` (`generateStaticParams`, JSON-LD, ingestion verisinden render) *(4 gün)*

---

## Toplam tahmini süre

| Phase | Süre |
|-------|------|
| Phase 0 — RAG Hardening | ~2 gün |
| Phase 1 — Demo (5 node + WS) | ~4 gün |
| Phase 2 — Neo4j (template Cypher) | ~4 gün |
| Phase 3 — Frontend (Next.js) | ~7 gün kaldı (~2.5 gün tamamlandı) |
| Phase 4 — Data Ingestion | ~7 gün |
| Phase 5 — Guardrails & Safety | ~4.5 gün |
| Phase 6 — Observability | ~3 gün |
| Phase 7 — Quality & Evaluation | ~5 gün |
| Phase 8 — Production Polish | ~6 gün |
| **Toplam (core)** | **~45 gün** |
| Stretch Goals (hepsi) | +20 gün |

Yarı zamanlı çalışmayla (~2-3 saat/gün) core ~7-9 hafta, stretch dahil ~11-13 hafta.

---

## Önerilen sıralar

**Kalite öncelikli:**

```
0 → 1 → 2 → 5 (basit guardrails) → 3 → 4 → 6 → 7 → 8
```

**Demo öncelikli (hızlı görsel sonuç):**

```
0 → 1 (graph + WS) → 3.D (FE WS bağla) → 2 (Neo4j) → 4 (ingestion) → 5 → 6 → 7 → 8
```

(3.B UI ✓. Sırada: Phase 1 — 5 node graph + WS backend, sonra 3.D FE'yi WS'e bağla.)

---

## Bağımlılık grafiği

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

Phase 3 ve Phase 4 birbirinden bağımsız, paralel ilerletilebilir.
