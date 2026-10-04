# Neo4j — v1 schema (design)

Goal: the graph produces **candidates + constraints**; Qdrant **explains**. Long prose is not stored here.

The LLM does not write Cypher. The planner picks a template + params; code runs safe Cypher.

---

## Relationship keys

```
IN_COUNTRY
IN_CITY
SERVED_BY_AIRPORT
CONNECTED_BY
DAY_TRIP_FROM
```

---

## Nodes

| Label | Identity / important fields |
|-------|-----------------------------|
| `Country` | `slug`, `name` |
| `City` | `slug`, `name`, `aliases[]`, `wikidata_qid`, `lat`, `lon`, `source_url` |
| `Place` | `slug`, `name`, `type`, `wikidata_qid`, `lat`, `lon`, `unesco`, `source_url` |
| `Airport` | `slug`, `name`, `iata`, `source_url` |

Example `Place.type` values: `museum` · `historical_site` · `attraction` · `natural_site` · `religious_site` · `archaeological_site`

---

## Relationships

| Key | Direction | Edge property (v1) |
|-----|-----------|-------------------|
| `IN_COUNTRY` | `(:City)-[:IN_COUNTRY]->(:Country)` | — |
| `IN_CITY` | `(:Place)-[:IN_CITY]->(:City)` | — |
| `SERVED_BY_AIRPORT` | `(:City)-[:SERVED_BY_AIRPORT]->(:Airport)` | — |
| `CONNECTED_BY` | `(:City)-[:CONNECTED_BY]->(:City)` | `mode`, `duration_min`, `duration_max`, `direct`, `source_url`, `evidence_text`, `confidence` |
| `DAY_TRIP_FROM` | `(:Place)-[:DAY_TRIP_FROM]->(:City)` | `one_way_minutes`, `mode`, `source_url`, `evidence_text`, `confidence` |

Durations are in **minutes**. If the text has none, use `null` — do not invent values.

---

## Intentionally out of v1

- `NEAR` edges (coordinate explosion)
- `Season` / `Activity` / `GOOD_FOR`
- `Region` / `District`
- Place→Place `PART_OF`
- LLM-generated Cypher

---

## Small example (Antalya)

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

(Perge)-[:IN_CITY]->(Antalya)          # geographic membership (province/city context)
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

Reading: *Perge is a day trip from Antalya.*

---

## Runtime (summary)

1. Planner: `needs_graph` + `graph_queries[{ template, params }]`
2. Code: fixed template Cypher
3. Output: Place/City id or name lists
4. Qdrant: text chunks for those candidates
5. Synthesize: duration/constraints (graph) + narrative (vector)

### Initial templates

| Template | Returns |
|----------|---------|
| `places_in_city` | Places in a city (optional `type` filter) |
| `day_trips_from_city` | `DAY_TRIP_FROM` with an upper bound on `one_way_minutes` |
| `connection_between_cities` | `CONNECTED_BY` between two cities |
| `airports_serving_city` | `SERVED_BY_AIRPORT` |

---

## Ingest principle

1. Deterministic: Country, City, Place (QID), Airport  
2. LLM (sparingly): **candidate** `CONNECTED_BY` / `DAY_TRIP_FROM` / `SERVED_BY_AIRPORT` from the Wikipedia city page  
3. Validate → evidence + confidence; duration `null` if missing  

The graph narrows the candidate set; Qdrant explains.

### Scripts (import)

| Script | Writes |
|--------|--------|
| `scripts/ingest-wiki-neo4j.py` | City, Country, `IN_COUNTRY`; optional Gemini relations (`wp_pages`) |
| `scripts/ingest-neo4j-places.py` | `Place` + `IN_CITY` (`wp_pois`) |

Flags: `--anchors-only`, `--wipe`, `--dry-run`, `--city`, `--country`.
