# Neo4j — v1 şema (tasarım)

Hedef: Graph **aday + kısıt** üretir; Qdrant **anlatır**. Uzun prose burada tutulmaz.

LLM Cypher yazmaz. Planner template + param seçer; kod güvenli Cypher çalıştırır.

---

## İlişki key’leri

```
IN_COUNTRY
IN_CITY
SERVED_BY_AIRPORT
CONNECTED_BY
DAY_TRIP_FROM
```

---

## Node’lar

| Label | Kimlik / önemli alanlar |
|-------|-------------------------|
| `Country` | `slug`, `name` |
| `City` | `slug`, `name`, `aliases[]`, `wikidata_qid`, `lat`, `lon`, `source_url` |
| `Place` | `slug`, `name`, `type`, `wikidata_qid`, `lat`, `lon`, `unesco`, `source_url` |
| `Airport` | `slug`, `name`, `iata`, `source_url` |

`Place.type` örnekleri: `museum` · `historical_site` · `attraction` · `natural_site` · `religious_site` · `archaeological_site`

---

## İlişkiler

| Key | Yön | Edge property (v1) |
|-----|-----|-------------------|
| `IN_COUNTRY` | `(:City)-[:IN_COUNTRY]->(:Country)` | — |
| `IN_CITY` | `(:Place)-[:IN_CITY]->(:City)` | — |
| `SERVED_BY_AIRPORT` | `(:City)-[:SERVED_BY_AIRPORT]->(:Airport)` | — |
| `CONNECTED_BY` | `(:City)-[:CONNECTED_BY]->(:City)` | `mode`, `duration_min`, `duration_max`, `direct`, `source_url`, `evidence_text`, `confidence` |
| `DAY_TRIP_FROM` | `(:Place)-[:DAY_TRIP_FROM]->(:City)` | `one_way_minutes`, `mode`, `source_url`, `evidence_text`, `confidence` |

Süreler **dakika**. Metinde yoksa `null` — uydurma yok.

---

## Bilerek v1 dışı

- `NEAR` edge (koordinat patlaması)
- `Season` / `Activity` / `GOOD_FOR`
- `Region` / `District`
- Place→Place `PART_OF`
- LLM-generated Cypher

---

## Küçük örnek (Antalya)

```
(:Country {slug: "turkey", name: "Turkey"})

(:City {
  slug: "antalya",
  name: "Antalya",
  aliases: ["Antalya"],
  wikidata_qid: "Q6487",
  source_url: "https://en.wikipedia.org/wiki/Antalya"
})

(:Airport {slug: "ayt", name: "Antalya Airport", iata: "AYT"})

(:Place {
  slug: "perge",
  name: "Perge",
  type: "archaeological_site",
  wikidata_qid: "Q217385",
  source_url: "https://en.wikipedia.org/wiki/Perga"
})

(:Place {
  slug: "antalya-museum",
  name: "Antalya Museum",
  type: "museum",
  source_url: "https://en.wikipedia.org/wiki/Antalya_Museum"
})
```

```
(Antalya)-[:IN_COUNTRY]->(Turkey)
(Antalya)-[:SERVED_BY_AIRPORT]->(AYT)

(Perge)-[:IN_CITY]->(Antalya)          # coğrafi aidiyet (il/şehir bağlamı)
(AntalyaMuseum)-[:IN_CITY]->(Antalya)

(Perge)-[:DAY_TRIP_FROM {
  one_way_minutes: 35,
  mode: "bus",
  evidence_text: "…",
  confidence: 0.9
}]->(Antalya)

(Istanbul)-[:CONNECTED_BY {
  mode: "flight",
  duration_min: 70,
  duration_max: 90,
  direct: true,
  confidence: 0.85
}]->(Antalya)
```

Okuma: *Perge is a day trip from Antalya.*

---

## Runtime (özet)

1. Planner: `needs_graph` + `graph_queries[{ template, params }]`
2. Kod: sabit template Cypher
3. Çıktan: Place/City id veya isim listesi
4. Qdrant: o adayların metin chunk’ları
5. Synthesize: süre/kısıt (graph) + anlatım (vector)

### İlk template’ler

| Template | Ne döner |
|----------|----------|
| `places_in_city` | Şehirdeki Place’ler (`type` filtre opsiyonel) |
| `day_trips_from_city` | `DAY_TRIP_FROM` + `one_way_minutes` üst sınır |
| `connection_between_cities` | İki city arası `CONNECTED_BY` |
| `airports_serving_city` | `SERVED_BY_AIRPORT` |

---

## Ingest ilkesi

1. Deterministik: Country, City, Place (QID), Airport  
2. LLM (az): Wikipedia şehir sayfasından **aday** `CONNECTED_BY` / `DAY_TRIP_FROM` / `SERVED_BY_AIRPORT`  
3. Validate → evidence + confidence; süre yoksa null  

Graph aday kümesini daraltır; Qdrant açıklar.

### Script’ler (import)

| Script | Ne yazar |
|--------|----------|
| `scripts/ingest-wiki-neo4j.py` | City, Country, `IN_COUNTRY`; opsiyonel Gemini ilişkileri (`wp_pages`) |
| `scripts/ingest-neo4j-places.py` | `Place` + `IN_CITY` (`wp_pois`) |

Flags: `--anchors-only`, `--wipe`, `--dry-run`, `--city`, `--country`.
