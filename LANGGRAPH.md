# Travel RAG — LangGraph Design

Geniş kapsamlı bir LangGraph pipeline tasarımı. Tek `chat` endpoint'i her tür soruyu (chitchat, faktoid, recommendation, planning, comparison, logistics, follow-up) karşılar; soru içindeki bilinmezleri çözer; hibrit (vector + graph) retrieval'a yönlendirir; self-reflection ile cevabı doğrular; WebSocket üzerinden adım adım stream eder.

Bu dosya **tasarım dokümanı**; uygulama `api/app/graph/` altında `state.py`, `nodes.py`, `edges.py`, `builder.py` olarak yapılır (bkz. `ROADMAP.md` Phase 1 & 2).

---

## State (TypedDict)

Tüm node'ların okuyup yazdığı ortak sözlük. Genel alanlar:

```
question        : str                  # son kullanıcı mesajı
history         : list[Message]        # önceki turlar
lang            : str                  # tr / en / …
intent          : Literal[...]         # chitchat | factoid | recommendation | planning | comparison | logistics | follow_up | meta
entities        : dict                 # cities, dates, season, budget, theme, audience, duration
slots           : dict                 # intent'e göre zorunlu alanlar + missing[]
assumptions     : list[str]            # soft-fallback ile yapılan varsayımlar (kullanıcıya bildirilecek)
plan            : str                  # insan-okunur plan, WS'e stream
sub_questions   : list[str]            # decomposition çıktısı
search_queries  : list[str]            # query_rewrite çıktısı
vector_chunks   : list[Chunk]          # Qdrant sonuçları
graph_results   : list[GraphHit]       # Neo4j sonuçları
external_data   : dict                 # hava, kur, mevsim (opsiyonel)
merged          : list[Chunk]          # birleştirilmiş + dedupe
reranked        : list[Chunk]          # top-N
answer          : str                  # final cevap
sources         : list[Source]         # citation listesi (S1, S2, …)
validations     : dict                 # groundedness, completeness skorları
iter            : int                  # replan döngü sayacı (max 2)
error           : str | None
latency_ms      : dict                 # node bazında ölçüm
```

---

## A. Intake & Guard (giriş katmanı)

### `intake_node` — Normalize & meta
Mesajı temizle, dil tespit et, uzunluk/encoding kontrolü, history'yi state'e koy. LLM çağırmaz; ucuz ve hızlı.

### `safety_guard_node` — Saldırı / kötüye kullanım
Prompt injection ("ignore previous…"), küfür, jailbreak. Rule-based + küçük classifier (Llama Prompt Guard 2-86M). Reddedilirse `reject_node`'a köprü.

### `domain_classifier_node` — Konu uygunluğu
"Bu seyahatle ilgili mi?" — kısa Qwen promptu veya mDeBERTa zero-shot. **No** ise `out_of_domain_node`.

---

## B. Understanding (anlama — bilinmezleri burada çöz)

### `context_resolver_node` — Coreference / pronoun çözümleme
"Orada ne yenir?", "Peki oradan kaç saat?" → chat history'den anaphora çöz. Önceki şehir/aktivite/tarih state'e yerleşir.

### `intent_classifier_node` — Soru tipi
`chitchat | factoid | recommendation | planning | comparison | logistics | follow_up | meta`. Plan ve route bu alana bakar.

### `entity_extraction_node` — Yapısal varlıklar
JSON çıkarımı: şehir, ülke, tarih, mevsim, bütçe seviyesi, yolcu profili (aile/yalnız), tema (deniz, tarih, doğa), süre (3 gün).

### `slot_filling_node` — Zorunlu alanlar tamam mı?
Intent başına gerekli slotları kontrol eder.
- `planning` → en az 1 şehir/bölge + süre
- `comparison` → en az 2 entity
- `logistics` → başlangıç + bitiş

Eksikse `slots.missing` doldurulur → `clarify_node`.

### `clarify_node` — Bilinmezi nasıl ele alır
İki strateji:
- **Soft fallback (default)**: makul varsayım yap, `assumptions[]`'a yaz, devam et ("süre belirtilmediği için 3 gün varsaydım")
- **Hard ask**: kritik eksik için kullanıcıya kısa soru WS event'i olarak dön, graph'ı duraklat

---

## C. Planning (plan katmanı — kullanıcıya hemen geri besle)

### `plan_node` — İnsan okunur plan
"Şu adımları izleyeceğim: A şehri için kaynaklar, B teması için graph araması, sonra özet." → WS'e anında stream. Plan-first reasoning'in görünür çıktısı.

### `decomposition_node` — Alt sorulara böl
Karmaşık sorular için 2–5 alt soru üretir. Her biri ileride ayrı retrieve.

Örnek: "ailece bütçe dostu Akdeniz" → `[ailece uygun yerler, bütçe dostu Akdeniz şehirleri, tarih+deniz birlikte yerler]`.

### `query_rewrite_node` — Arama sorgusu üret
Doğal dilden retrieval-optimized sorgular. Embedding ve Cypher için ayrı stringler. Multi-turn'da: history + son soru → tek "self-contained" arama metni.

---

## D. Routing (yönlendirme — node değil, conditional edge)

### `retrieval_router` (edge)
`intent + entity + decomposition` çıktısına göre patika seçer:

| Koşul | Patika |
|--------|--------|
| `chitchat` / `meta` | `chitchat_node` |
| Basit faktoid | sadece `vector_retrieval` |
| Structured constraint (UNESCO, mesafe, bütçe) | sadece `graph_retrieval` |
| Karmaşık / multi-constraint | hybrid (vector + graph paralel) |
| Tarih/hava bağımlı | `external_data_node` de paralele eklenir |

---

## E. Retrieval (paralel olabilir)

### `vector_retrieval_node` — Qdrant
Alt soru başına embed + top-k. `min_score` filtre, dedup. Çıktı: `vector_chunks`.

### `graph_retrieval_node` — Neo4j (iki alt adım)

- **`cypher_generation_node`** — Qwen + few-shot prompt. **MATCH/RETURN-only** guard ile yazma yasak.
- **`cypher_execute_node`** — driver ile çalıştır, sonuçları normalize et. Çıktı: `graph_results`.

### `external_data_node` — Canlı veri (opsiyonel, stretch)
Tarih bağımlı sorular: hava, mevsim, döviz. Bu olmadan "Şu an sıcak mı?", "Euro kaç TL?" cevaplanamaz.

### `merge_dedupe_node` — Sonuçları birleştir
Aynı kaynak (city + section) tek sefer; vector + graph sonuçlarını ortak `Chunk` şemasına çevir. Çıktı: `merged`.

### `rerank_node` — Yeniden sırala
Cross-encoder veya LLM-based küçük puanlama. Hibrit sonuçlar için kritik. Top-N kes. Çıktı: `reranked`.

### `coverage_check_node` — Yeterli kaynak var mı?
Alt sorulardan hangileri boş kaldı? 
- Hiç sonuç yok → `no_results_node` ("bu konuda bilgim yok, şunu deneyebiliriz…")
- Kısmen eksik → `replan_router` (loop edge)

---

## F. Synthesis (cevap üretimi)

### `chitchat_node` — Kısa LLM, retrieval yok
"Merhaba", "neler yapabilirsin" → direkt cevap, kaynak yok.

### `synthesize_node` — Asıl cevap
Kaynakları `[S1] [S2]` ile numaralandır, "uydurma yok" talimatı, token-by-token WS stream. Çıktı: `answer` + `sources`.

### `comparison_synthesize_node` — Karşılaştırma şablonu
İki+ entity için tablo/skor formatı. `intent == comparison` patikasında devreye girer.

### `planner_synthesize_node` — Çok günlük rota
Gün/zaman-blokları ile yapılandırılmış çıktı. `intent == planning` için.

### `out_of_domain_node` / `reject_node` — Hazır yanıt
Saldırı veya konu dışı için sabit, çevirilmiş mesaj. LLM çağrısı opsiyonel — performans için template tercih edilir.

---

## G. Self-reflection (kalite döngüsü)

> **Not:** Bu adımlar Phase 7 (eval altyapısı) sonrası güvenli açılır; ondan önce loop riski var.

### `groundedness_check_node` — Kaynak desteği
LLM-as-judge mini-prompt: "Şu cevap şu kaynaklardan destekleniyor mu?" → 0–1 skor.

### `completeness_check_node` — Alt sorular cevaplandı mı?
Plan'daki adımlardan eksik kalan var mı? Boolean / liste.

### `replan_router` (conditional edge)
- `groundedness < eşik` **veya** completeness eksik **ve** `iter < 2` → `decomposition_node`'a geri dön
- Yeterli → `output_safety_node`

---

## H. Post-processing (çıkış katmanı)

### `output_safety_node` — PII / toksik / hassas içerik
Üretilen cevapta filtre. Phase 5'in çıkış tarafı. PII bulursa maskele.

### `format_node` — UI'a hazır şekil
Markdown, citation map (`[S1]` → source dict), `plan` field, `assumptions`, latency, model bilgisi. `ChatResponse`'a koy.

### `memory_update_node` — Konuşma belleği
Son user+assistant mesajını state'e + (opsiyonel) Neo4j user node'una yaz. Sonraki turda `context_resolver` bunu okur.

---

## Akış şeması (kuş bakışı)

```
intake → safety_guard → domain_classifier
   ↓                            ↘ out_of_domain → format
context_resolver → intent_classifier → entity_extraction → slot_filling
                                                              ↓
                                                  (eksik?) clarify_node ─┐
                                                              ↓          │
                              plan_node ─────► (WS plan event)           │
                                ↓                                         │
                          decomposition → query_rewrite                   │
                                ↓                                         │
                  retrieval_router (conditional edge)                     │
                  ├── chitchat_node ────────────────┐                     │
                  ├── vector_retrieval ─┐           │                     │
                  ├── graph_retrieval ──┤           │                     │
                  └── external_data ────┤           │                     │
                                        ↓           │                     │
                              merge_dedupe → rerank │                     │
                                        ↓           │                     │
                              coverage_check        │                     │
                                        ↓           │                     │
                              synthesize_node ◄─────┘                     │
                                        ↓                                 │
                  groundedness_check + completeness_check                 │
                                        ↓                                 │
                            replan_router ─── (loop ≤2) ─────► decomposition
                                        ↓ yeterli                        │
                              output_safety → format → memory_update ◄───┘
                                        ↓
                                    final answer (WS done)
```

---

## WebSocket event'leri

Her node bir adım event'i emit eder; UI thinking steps olarak gösterir.

| Event | Ne zaman | Payload |
|--------|----------|---------|
| `intake` | İlk node | `{ lang }` |
| `plan` | `plan_node` çıkışı | `{ plan: "..." }` |
| `step` | Her node start/end | `{ node, status: "running"\|"done", label, ms }` |
| `clarify` | `clarify_node` hard ask | `{ question, options? }` |
| `token` | `synthesize` token stream | `{ delta }` |
| `done` | `format_node` sonu | `{ answer, sources, plan, assumptions, latency_ms }` |
| `error` | Herhangi bir node hata | `{ message, node }` |

WS bağlantısı `done` veya `error` sonrası kapanır.

---

## Bilinmezleri çözme stratejisi (özet)

| Bilinmez tipi | Çözüm |
|---------------|--------|
| Anaphora ("orada", "oradan") | `context_resolver_node` history'den çözer |
| Eksik zorunlu slot (süre, şehir, bütçe) | `slot_filling_node` tespit → `clarify_node` (soft default veya hard ask) |
| Multi-constraint karmaşık soru | `decomposition_node` alt sorulara böler |
| Zaman/hava bağımlı ("şu an") | `external_data_node` canlı veri |
| Yanlış / yetersiz cevap | `groundedness` + `completeness` → `replan_router` loop |

---

## Phasing (Roadmap ile eşleşme)

| Phase | Eklenen node'lar |
|-------|------------------|
| **1.A** | `intake`, `plan`, `vector_retrieval`, `synthesize`, `format` |
| **1.B** | `intent_classifier`, `retrieval_router`, `chitchat`, `comparison_synthesize`, `planner_synthesize` |
| **2** | `entity_extraction`, `cypher_generation`, `cypher_execute`, `merge_dedupe`, `rerank`, `coverage_check` |
| **5** | `safety_guard`, `domain_classifier`, `out_of_domain`, `output_safety` |
| **7+** | `groundedness_check`, `completeness_check`, `replan_router` (eval altyapısı önce) |
| **Stretch S.1 / S.2** | `context_resolver`, `slot_filling`, `clarify`, `memory_update`, `external_data` |

---

## Tasarım notları

- **State minimal başlasın**, her phase yeni alan ekler.
- **Her LLM node'u** `llm.generate()` üzerinden geçer — tek Semaphore(1) yeter; sıra fairness korunur.
- **Loop limiti** (`iter < 2`) sonsuz döngü riskini engeller.
- **WS event'leri** her node'da emit edilir; sıralı stream UX'in temelidir.
- **Node'lar saf fonksiyon gibi**: state in → state out. Yan etki yalnızca WS event ve dış servis çağrısı.
- **Test edilebilirlik**: her node ayrı unit test (mock LLM/Qdrant/Neo4j); graph compile sonrası integration test.
