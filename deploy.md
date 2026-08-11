# Travel RAG — Sunucu kurulum (`install.md`)

Proje kökünden çalıştır. Varsayılanlar `docker-compose.yml` + `.env.example` ile uyumludur.

---

## 1. Önkoşullar

- Docker + Docker Compose
- (Ingest için) Python 3.11+ veya 3.13, proje `.venv`
- (Opsiyonel FE) Node.js 18+
- Gemini kullanacaksan: API key

---

## 2. Repo ve yapılandırma

```bash
cd /path/to/travel-rag

cp .env.example .env
# Üretimde LLM_PROVIDER=gemini önerilir; geliştirmede qwen de olur.

mkdir -p ~/.config/rag
# Gemini:
echo 'GEMINI_API_KEY=your_key_here' >> ~/.config/rag/.env
chmod 600 ~/.config/rag/.env
```

Not: API container secrets’i `${HOME}/.config/rag` → `/config/rag` mount eder.

---

## 3. Docker volume (Ollama)

```bash
docker volume create qwen_ollama_models
```

---

## 4. Servisleri başlat

```bash
docker compose up -d --build
docker compose ps
```

MariaDB kullanıcı/DB adı `travel` / `travel`. Eski volume (farklı DB/user ile init edilmiş) kullanıyorsan sıfırlamak gerekir:

```bash
docker compose down
docker volume ls | grep mariadb   # örn. travel-rag_mariadb_data
docker volume rm <mariadb_volume>
docker compose up -d
# ardından §6 ingest’i yeniden çalıştır
```

Servisler:

| Servis | Port (host) |
|--------|-------------|
| API | http://localhost:8000 |
| Qdrant | http://localhost:6333 |
| Neo4j Browser | http://localhost:7474 |
| Neo4j Bolt | localhost:7687 |
| MariaDB | localhost:3306 |
| Ollama | http://127.0.0.1:11434 |

---

## 5. Ollama modelleri

```bash
./scripts/init-ollama.sh
# Embeddings: bge-m3
# Guard: sileader/qwen3guard:0.6b
# İsteğe bağlı Qwen (LLM_PROVIDER=qwen)
```

---

## 6. Ingest venv (host → localhost portları)

```bash
./scripts/setup-venv.sh

export MARIADB_HOST=localhost
export MARIADB_USER=travel
export MARIADB_PASSWORD=changeme
export MARIADB_DATABASE=travel
export QDRANT_URL=http://localhost:6333
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_EMBED_MODEL=bge-m3
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=changeme
```

Kaynak JSON: `data/wiki/sources.json`, `museums.json`, `historical_sites.json`.

### 6a. Guide sayfalar → MariaDB → Qdrant

```bash
# Tüm city pages (uzun sürebilir)
.venv/bin/python scripts/parse-wiki-pages.py --pages-only

# Qdrant travel_chunks (+ wp_section_stats; varsayılan wipe)
.venv/bin/python scripts/ingest-wiki-qdrant.py
```

Guide (`wp_pages`) chunk’ları yalnızca allowlist section’lara yazılır
(`scripts/lib/wp_chunks.py` → `SECTION_ALLOWLIST`; payload’da `section_group`).
POI sayfaları filtrelenmez.

Tek şehir deneme:

```bash
.venv/bin/python scripts/parse-wiki-pages.py --country Turkey --city Istanbul --pages-only
.venv/bin/python scripts/ingest-wiki-qdrant.py --city Istanbul
```

### 6b. Neo4j (City / ilişkiler)

```bash
.venv/bin/python scripts/ingest-wiki-neo4j.py --anchors-only
# Gemini ile ilişkiler:
LLM_PROVIDER=gemini .venv/bin/python scripts/ingest-wiki-neo4j.py --city Istanbul
```

### 6c. Müzeler / tarihi yerler → Qdrant + Neo4j Place

```bash
.venv/bin/python scripts/parse-wikipedia-pois.py
.venv/bin/python scripts/ingest-wikipedia-pois.py
.venv/bin/python scripts/ingest-neo4j-places.py
```

---

## 7. Frontend (opsiyonel)

```bash
cd web
npm install
cp .env.local.example .env.local
# NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
# http://localhost:3000
```

API kod değişince:

```bash
docker compose up -d --build api
```

---

## 8. Sistem durumu — test / linkler

### Linkler

- Sohbet UI: http://localhost:3000
- API: http://localhost:8000
- Qdrant dashboard: http://localhost:6333/dashboard
- Neo4j Browser: http://localhost:7474 (user `neo4j` / `changeme`)

### Health / compose

```bash
docker compose ps
curl -s http://localhost:8000/health | head
curl -s http://localhost:8000/docs | head -5
curl -s http://localhost:6333/collections
curl -s http://127.0.0.1:11434/api/tags
docker exec travel-rag-neo4j cypher-shell -u neo4j -p changeme 'RETURN 1 AS ok'
docker exec travel-rag-mariadb mariadb -utravel -pchangeme travel -e 'SELECT 1'
```

### Hızlı sohbet smoke

Tarayıcıda http://localhost:3000 → örn. `Istanbul airports`  
veya WebSocket istemcinizle `WS /ws/chat`.

---

## 9. Veritabanı / indeks sorguları

### Qdrant — koleksiyonlar ve kayıt sayısı

```bash
curl -s http://localhost:6333/collections | python3 -m json.tool

curl -s http://localhost:6333/collections/travel_chunks | \
  python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print('points', r.get('points_count'))"

curl -s http://localhost:6333/collections/travel_museums | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('points_count', 'missing'))"
```

Örnek noktalar (payload):

```bash
curl -s -X POST http://localhost:6333/collections/travel_chunks/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{"limit":5,"with_payload":["title","city_name","section","source_url","source"],"with_vector":false}' \
  | python3 -m json.tool
```

Şehir filtresi:

```bash
curl -s -X POST http://localhost:6333/collections/travel_chunks/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 10,
    "with_payload": ["title","section","source_url"],
    "with_vector": false,
    "filter": {"must":[{"key":"city_name","match":{"value":"Istanbul"}}]}
  }' | python3 -m json.tool
```

### MariaDB — `wp_section_stats`

```bash
docker exec -i travel-rag-mariadb mariadb -utravel -pchangeme travel <<'SQL'
SELECT section, chunk_count, page_count, updated_at
FROM wp_section_stats
ORDER BY chunk_count DESC
LIMIT 30;
SQL
```

### MariaDB — `wp_pages`

```bash
docker exec -i travel-rag-mariadb mariadb -utravel -pchangeme travel <<'SQL'
SELECT id, page_kind, country_name, city_name, title,
       CHAR_LENGTH(html) AS html_chars, fetched_at, source_url
FROM wp_pages
ORDER BY country_name, city_name
LIMIT 50;

SELECT page_kind, COUNT(*) AS n
FROM wp_pages
GROUP BY page_kind;
SQL
```

### (Bonus) Neo4j şehir sayısı

```bash
docker exec -i travel-rag-neo4j cypher-shell -u neo4j -p changeme \
  'MATCH (c:City) RETURN count(c) AS cities;'
```

---

## 10. Retrieval eval (Hit@k / Recall@k / Precision@k / MRR)

Qdrant + embed ayaktayken (`travel_chunks` dolu):

```bash
QDRANT_URL=http://localhost:6333 OLLAMA_BASE_URL=http://localhost:11434 \
  .venv/bin/python eval/run_retrieval_eval.py

# Opsiyonel:
# .venv/bin/python eval/run_retrieval_eval.py --k 10 --limit 3
# .venv/bin/python eval/validate_golden.py
```

Golden: `eval/golden/retrieval.json` (`source_url` + `section`).  
Çıktı: `eval/results/<timestamp>_<model>_kN.json` (gitignored).  
Eval unreachable golden’ları skordan düşer; retrieval varsayılan olarak `source_url+section` dedupe eder.

---

## İlgili dokümanlar

- `README.md` — genel bakış
- `LANGGRAPH.md` — runtime pipeline
- `neo4j.md` — graph şema
