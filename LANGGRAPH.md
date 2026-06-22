# Travel RAG — LangGraph Design

Bu dosya iki katmandan oluşur:

1. **Phase 1 — Minimal graph (MVP)** — İlk canlı demo; 5 node, 2 LLM çağrısı, Qdrant-only. **Bunu önce implement et.**
2. **Target architecture** — Kuzey yıldızı; tüm node'lar, Neo4j, guardrails, self-reflection. Zamanla ekle.

Uygulama: `api/app/graph/` → `state.py`, `nodes.py`, `edges.py`, `builder.py` (bkz. `ROADMAP.md`).

---

## Phase 1 — Minimal graph (implement first)

Lokal Ollama/Qwen yavaş; her şeyi ayrı LLM node yapma. İlk sürüm **tek planner LLM + tek synthesize LLM**.

### Graph (linear)

```
START → intake_node → planner_json_node → vector_retrieval_node → synthesize_node → format_node → END
```

| Node | LLM? | WS event |
|------|------|----------|
| `intake_node` | Hayır | — (sessiz) |
| `planner_json_node` | **Evet (1)** | önce `status`, sonra `plan` |
| `vector_retrieval_node` | Hayır | `step` (s1 running/done) |
| `synthesize_node` | **Evet (2)** stream | `step` (s2) + `token` |
| `format_node` | Hayır | — (sessiz); `done` + WS close |

**Toplam LLM çağrısı:** 2 (planner + synthesize). Semaphore(1) ile sıralı.

### `planner_json_node` — Tek LLM, çoklu çıktı

`intent_classifier`, `entity_extraction`, `plan_node`, `decomposition`, `query_rewrite` **ayrı node değil** — hepsi bu tek çağrıda JSON olarak döner:

```jsonc
{
  "intent": "recommendation",
  "entities": {
    "cities": ["Antalya"],
    "theme": ["family", "beach"]
  },
  "assumptions": [
    "Bütçe belirtilmediği için orta bütçe varsaydım."
  ],
  "plan": {
    "summary": "Antalya'da aileyle gidilecek yerleri araştıracağım.",
    "steps": [
      { "id": "s1", "label": "İlgili kaynakları ara", "kind": "vector", "status": "idle" },
      { "id": "s2", "label": "Cevabı hazırla", "kind": "synthesize", "status": "idle" }
    ]
  },
  "search_queries": [
    "Antalya aileyle gezilecek yerler",
    "Antalya çocukla uygun plajlar ve aktiviteler"
  ],
  "answer_style": "recommendation"
}
```

Phase 1 kuralları:
- **Hard ask yok** — `clarify_node` kullanılmaz; eksik bilgi `assumptions[]` ile soft fallback
- **Neo4j yok** — sadece Qdrant
- **rerank yok** — top-k + basit dedup yeter
- **groundedness / completeness yok** — Phase 7+ (eval altyapısı sonrası)

### Phase 1 node detayları

#### `intake_node`
Mesajı normalize et, `history`'yi state'e koy, dil tespiti (basit). LLM yok.

#### `vector_retrieval_node`
`search_queries[]` için Qdrant araması (her query embed + top-k, birleştir, dedup). `sources` state'e yazılır.

#### `synthesize_node`
`reranked` / `vector_chunks` + `assumptions` + `plan.summary` ile cevap üret. Token stream → WS `token` event'leri.

#### `format_node`
`ChatResponse` şekline getir (`answer`, `sources`, `plan`, `assumptions`, `latency_ms`). WS `done` emit et; server bağlantıyı kapatır.

---

## Target architecture (kuzey yıldızı — sonra ekle)

Aşağıdaki bölümler (A–H) tam ürün hedefidir. Phase 1 demo çalıştıktan sonra kademeli eklenir (bkz. [Phasing](#phasing-roadmap-ile-eşleşme)).

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
plan            : Plan                 # yapısal plan (aşağı bkz.) — WS'e stream
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

### Plan şeması (JSON)

`planner_json_node` (Phase 1) veya hedef mimaride `plan_node` çıktısı; UI'da plan card olarak gösterilir.

```jsonc
{
  "summary": "Antalya'da aileyle gidilecek yerleri araştıracağım.",
  "steps": [
    { "id": "s1", "label": "İlgili kaynakları ara", "kind": "vector",     "status": "idle" },
    { "id": "s2", "label": "Cevabı hazırla",         "kind": "synthesize", "status": "idle" }
  ]
}
```

Phase 1'de genelde 2 step yeter (vector + synthesize). Neo4j eklendiğinde `graph`, `merge` step'leri de gelir.

- `kind`: `vector | graph | external | reasoning | merge | synthesize | clarify`
- `status`: `idle | running | done | error` — node event'leri ile güncellenir

---

## A. Intake & Guard (giriş katmanı) — *Phase 5*

### `intake_node` — Normalize & meta
Mesajı temizle, dil tespit et, uzunluk/encoding kontrolü, history'yi state'e koy. LLM çağırmaz; ucuz ve hızlı.

### `safety_guard_node` — Saldırı / kötüye kullanım
Prompt injection ("ignore previous…"), küfür, jailbreak. Rule-based + küçük classifier (Llama Prompt Guard 2-86M). Reddedilirse `reject_node`'a köprü.

### `domain_classifier_node` — Konu uygunluğu
"Bu seyahatle ilgili mi?" — kısa Qwen promptu veya mDeBERTa zero-shot. **No** ise `out_of_domain_node`.

---

## B. Understanding (anlama — bilinmezleri burada çöz) — *Phase 4+ (Phase 1'de `planner_json_node` içinde)*

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

### `clarify_node` — Bilinmezi nasıl ele alır — *Phase 4+*
İki strateji:
- **Soft fallback (Phase 1 default)**: makul varsayım yap, `assumptions[]`'a yaz, devam et
- **Hard ask (Phase 4+)**: kritik eksik için kullanıcıya kısa soru WS `clarify` event'i; graph duraklar

Phase 1'de yalnızca soft fallback kullanılır.

---

## C. Planning (plan katmanı) — *Phase 1: `planner_json_node`; hedefte ayrı node'lar*

### `plan_node` — Hedef mimaride ayrı node
Phase 4+'ta `planner_json_node` parçalanabilir. Phase 1'de plan, intent, entities tek JSON'da gelir (yukarı bak).

### `decomposition_node` — Alt sorulara böl — *Phase 4+*
Karmaşık sorular için 2–5 alt soru üretir. Her biri ileride ayrı retrieve.

Örnek: "ailece bütçe dostu Akdeniz" → `[ailece uygun yerler, bütçe dostu Akdeniz şehirleri, tarih+deniz birlikte yerler]`.

### `query_rewrite_node` — Arama sorgusu üret — *Phase 2+ (Phase 1'de `planner_json_node.search_queries`)*
Doğal dilden retrieval-optimized sorgular. Embedding ve Cypher için ayrı stringler. Multi-turn'da: history + son soru → tek "self-contained" arama metni.

---

## D. Routing (yönlendirme) — *Phase 4+*

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

## E. Retrieval — *Phase 1: vector only; Phase 3: Neo4j*

### `vector_retrieval_node` — Qdrant
Alt soru başına embed + top-k. `min_score` filtre, dedup. Çıktı: `vector_chunks`.

### `graph_retrieval_node` — Neo4j — *Phase 3*

**Phase 3 başlangıç:** hazır Cypher template'leri (LLM Cypher generation sonra):
- `intent == distance` → mesafe/komşuluk template
- `intent == nearby_unesco` → UNESCO yakınlık template
- `intent == airport_access` → havalimanı erişim template

**Phase 3 ileri:** `cypher_generation_node` (Qwen + few-shot) + validation guard

### `external_data_node` — Canlı veri (opsiyonel, stretch)
Tarih bağımlı sorular: hava, mevsim, döviz. Bu olmadan "Şu an sıcak mı?", "Euro kaç TL?" cevaplanamaz.

### `merge_dedupe_node` — Sonuçları birleştir
Aynı kaynak (city + section) tek sefer; vector + graph sonuçlarını ortak `Chunk` şemasına çevir. Çıktı: `merged`.

### `rerank_node` — Yeniden sırala — *Phase 4+ (Phase 1'de yok)*

Cross-encoder veya LLM-based puanlama. Hibrit sonuçlar için; ilk sürümde Qdrant top-k + dedup yeter.

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

## G. Self-reflection — *Phase 7+ (eval altyapısı önce; erken açma)*

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

## Akış şeması — Phase 1 (minimal)

```
START → intake → planner_json → vector_retrieval → synthesize → format → END
         (sessiz)   status+plan      step s1           step s2+token   done+close
```

## Akış şeması — Target architecture (kuzey yıldızı)

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

## WebSocket protokolü

### Bağlantı modeli — single-shot

- **Bir soru = bir WebSocket bağlantısı.** Client `WS /ws/chat`'i açar, ilk frame'de soruyu gönderir.
- Server `done` veya `error` event'i sonrası bağlantıyı **kendi kapatır**.
- Yeni soru için client yeniden bağlanır. Bu, backpressure'ı basitleştirir ve Semaphore(1) modeline uyar.
- Server tarafı, `astream_events()`'ten gelen LangGraph event'lerini protokole çevirip yazar.

### İstemci → sunucu

```jsonc
{ "type": "message", "content": "Antalya'da aileyle gidilecek yerler?", "history": [...] }
```

### Sunucu → istemci event'leri

| Event | Ne zaman | Payload |
|--------|----------|---------|
| `status` | **Hemen** — WS açılır açılmaz (LLM beklemeden) | `{ message: "Sorunu analiz ediyorum..." }` |
| `plan` | `planner_json_node` biter bitmez | `{ summary, steps[] }` |
| `step` | Anlamlı node start/end | `{ step_id, node, status: "running"\|"done"\|"error", label, detail?, ms }` |
| `clarify` | `clarify_node` hard ask *(Phase 4+)* | `{ question, options?, step_id }` |
| `token` | `synthesize` sırasında her token | `{ delta }` |
| `done` | `format_node` çıkışı (server WS'i kapatır) | `{ answer, sources, plan, assumptions, latency_ms }` |
| `error` | Herhangi bir node patlarsa (server WS'i kapatır) | `{ message, step_id?, node? }` |

**Phase 1 emit eden node'lar:** `planner_json` → `status` + `plan`; `vector_retrieval` → `step`; `synthesize` → `step` + `token`; `format` → `done`.

### Hangi node'lar event yayar? (target architecture)

Her node değil — sadece **kullanıcıya değer üretenler**. Bu, ekranda flicker'ı engeller.

- ✅ **Emit eder**: `plan`, `decomposition`, `vector_retrieval`, `graph_retrieval`, `cypher_generation`, `external_data`, `merge_dedupe`, `rerank`, `synthesize`, `groundedness_check`, `clarify`, `out_of_domain`
- 🔇 **Sessiz** (sadece log/trace): `intake`, `safety_guard`, `domain_classifier`, `context_resolver`, `intent_classifier`, `entity_extraction`, `slot_filling`, `query_rewrite`, `coverage_check`, `completeness_check`, `output_safety`, `format`, `memory_update`

Sessiz node'lar kısa ve değişmez (genelde <100 ms); kullanıcıya "Düşünüyor" eklemek değer katmaz.

---

## Chain-of-Thought UX (kullanıcıya görünür ilerleme)

> Önemli: Bu, modelin **ham iç düşüncesini** (gerçek CoT) göstermek değildir. Yalnızca pipeline'ın **anlamlı adımları** ve LLM'in ürettiği plan özeti görünür. Ham reasoning trace kapalı kalır (güvenlik + UX).

### Akış (Phase 1)

1. Kullanıcı soruyu gönderir → WS açılır
2. **`status` event** — anında ("Sorunu analiz ediyorum…")
3. **`plan` event** — planner LLM biter (~1–2 sn); `PlanCard` açılır
4. `vector_retrieval`: `step:running` → gri Thinking… → `step:done` + detail
5. `synthesize`: `step:running` → ilk **`token`** → Thinking silinir, asistan baloncuğu açılır
6. **`done`** → sources; plan card tamamlandı; **server WS kapatır**

### Hata yakalama

- Bir node `error` event yollarsa:
  - O step kart üzerinde **kırmızı** işaretle gösterilir
  - Asistan baloncuğu açılır ve içinde "Üzgünüm, `{step.label}` adımında hata oldu" yazısı görünür
  - Server WS'i kapatır; client retry butonu gösterebilir
- Beklenmedik (ağ kopması, server crash) → client `onclose` ile aynı asistan hata mesajını basar

### UI render kuralı (özet)

| Event | UI etkisi |
|-------|-----------|
| `status` | Gri durum satırı veya spinner ("Sorunu analiz ediyorum…") |
| `plan` | `PlanCard` açılır; `steps[]` listelenir, hepsi `idle` |
| `step:running` (synthesize değil) | İlgili step `running` görünür; ek olarak alt satıra gri "Thinking…" |
| `step:done` | Step `done`, satır `detail` ile güncellenir |
| `step:error` | Step kırmızı; asistan baloncuğunda hata mesajı |
| `step:running` (synthesize) | Sentez başladı; Thinking satırı görünür |
| ilk `token` | Thinking satırı silinir, asistan baloncuğu açılır |
| sonraki `token` | Baloncuğa append |
| `done` | Sources rendered; plan card "tamamlandı" |
| WS close | Loading state biter; input tekrar aktif |

---

## Bilinmezleri çözme stratejisi (özet)

| Bilinmez tipi | Çözüm |
|---------------|--------|
| Anaphora ("orada", "oradan") | `context_resolver_node` history'den çözer |
| Eksik zorunlu slot (süre, şehir, bütçe) | Phase 1: `assumptions[]` soft fallback; Phase 4+: `clarify_node` |
| Multi-constraint karmaşık soru | `decomposition_node` alt sorulara böler |
| Zaman/hava bağımlı ("şu an") | `external_data_node` canlı veri |
| Yanlış / yetersiz cevap | `groundedness` + `completeness` → `replan_router` loop |

---

## Phasing (Roadmap ile eşleşme)

| Phase | Ne eklenir |
|-------|------------|
| **1 — Demo** | 5 node: `intake` → `planner_json` → `vector_retrieval` → `synthesize` → `format`; WS (`status`, `plan`, `step`, `token`, `done`, `error`); Qdrant RAG |
| **1.C — RAG kalite** | Citation `[S1]`, basit dedup, `latency_ms`, boş retrieval handling |
| **2 — Neo4j** | `graph_store`, template Cypher, `retrieval_router`, hybrid merge; LLM Cypher → 2.D veya sonrası |
| **3 — FE (3.D)** | `useChatStream`, `PlanCard`, `status` satırı, thinking line, token bubble, retry |
| **4 — Ingestion** (ROADMAP) | Wikivoyage verisi; graph kodu değişmez |
| **Adv. graph** (Neo4j sonrası) | `context_resolver`, ayrı intent/entity node'ları, `rerank`, `coverage_check`, router, `clarify` hard ask |
| **5 — Safety** (ROADMAP) | `safety_guard`, `domain_classifier`, `output_safety`; **`POST /chat` kaldır** |
| **7+ — Self-reflection** | `groundedness_check`, `completeness_check`, `replan_router` |
| **Stretch** | `memory_update`, `external_data`, multi-turn history |

---

## Tasarım notları

- **Phase 1 = 2 LLM çağrısı** — `planner_json_node` + `synthesize_node`; ayrı intent/entity/plan/rewrite node'ları Phase 4'e ertelendi.
- **`status` event** plan'dan önce gelir — boş ekran önlenir.
- **Her LLM node'u** `llm.generate()` üzerinden geçer — tek Semaphore(1) yeter; sıra fairness korunur.
- **Loop limiti** (`iter < 2`) sonsuz döngü riskini engeller.
- **WS event'leri yalnızca anlamlı node'larda** emit edilir (yukarıdaki listeye bak); sıralı stream UX'in temelidir.
- **WS bağlantısı single-shot** — server `done`/`error` sonrası kapatır; client her soru için yeniden bağlanır.
- **Plan JSON** state içinde tutulur ve UI'daki her step `step_id` ile eşlenir; bu, UI ile graph arasındaki tek doğruluk kaynağıdır.
- **`/chat` REST endpoint'i** sadece test/Postman amaçlıdır; Phase 5'te kaldırılır (ROADMAP 5.10).
- **Node'lar saf fonksiyon gibi**: state in → state out. Yan etki yalnızca WS event ve dış servis çağrısı.
- **Test edilebilirlik**: her node ayrı unit test (mock LLM/Qdrant/Neo4j); graph compile sonrası integration test.
