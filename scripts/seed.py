"""
Seed script for Qdrant and Neo4j.

Populates the vector store with tourism text chunks and
the knowledge graph with entities and relationships.
Creates SourceChunk nodes in Neo4j to bridge both databases.

Usage:
    ALLOW_DB_RESET=true QDRANT_URL=http://localhost:6333 \
    NEO4J_URI=bolt://localhost:7687 \
    OLLAMA_BASE_URL=http://localhost:11434 \
    python scripts/seed.py
"""

import asyncio
import os
import sys
import uuid

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ALLOW_DB_RESET = os.getenv("ALLOW_DB_RESET", "false").lower() == "true"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "tourism_chunks")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

CHUNKS = [
    {
        "text": "Antalya, Türkiye'nin güneyinde Akdeniz kıyısında yer alan bir tatil şehridir. Yılda 300 günden fazla güneş alır. Tarihi Kaleiçi bölgesi, antik limanı ve dar sokakları ile ünlüdür.",
        "city_qid": "Q6487",
        "city_name": "Antalya",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "overview",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Antalya",
        "lang": "tr",
    },
    {
        "text": "Kaleiçi, Antalya'nın tarihi merkezi olup Roma, Bizans ve Osmanlı dönemlerinden kalma yapıları barındırır. Hadrian Kapısı MS 130 yılında inşa edilmiştir. Bölge UNESCO Dünya Mirası geçici listesindedir.",
        "city_qid": "Q6487",
        "city_name": "Antalya",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Antalya",
        "lang": "tr",
    },
    {
        "text": "Side antik kenti, Antalya'ya 75 km uzaklıkta bir sahil kasabasıdır. Roma dönemi tiyatrosu 15.000 kişi kapasitelidir. Apollon Tapınağı gün batımı manzarasıyla ünlüdür.",
        "city_qid": "Q152405",
        "city_name": "Side",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Side",
        "lang": "tr",
    },
    {
        "text": "Kapadokya, Türkiye'nin Nevşehir ilinde yer alan volkanik bir bölgedir. Peri bacaları, yeraltı şehirleri ve sıcak hava balonu turlarıyla tanınır. Göreme Açık Hava Müzesi UNESCO Dünya Mirası listesindedir.",
        "city_qid": "Q6710593",
        "city_name": "Göreme",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "overview",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Kapadokya",
        "lang": "tr",
    },
    {
        "text": "İstanbul Boğazı, Asya ve Avrupa kıtalarını ayıran doğal bir su yoludur. Boğaz turu tekneleri Eminönü'nden kalkar. Kız Kulesi ve Rumeli Hisarı rotanın önemli noktalarıdır.",
        "city_qid": "Q406",
        "city_name": "İstanbul",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "activity",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/İstanbul",
        "lang": "tr",
    },
    {
        "text": "Efes antik kenti, İzmir'in Selçuk ilçesinde yer alır. Celsus Kütüphanesi ve Büyük Tiyatro en bilinen yapılardır. Roma döneminde 250.000 nüfuslu bir metropoldü. UNESCO Dünya Mirası listesindedir.",
        "city_qid": "Q876176",
        "city_name": "Selçuk",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Selçuk",
        "lang": "tr",
    },
    {
        "text": "Pamukkale, Denizli'de yer alan beyaz travertenlerden oluşan doğal bir oluşumdur. Termal suları 2000 yıldan fazla süredir kullanılmaktadır. Hierapolis antik kenti de aynı alandadır. UNESCO Dünya Mirası listesindedir.",
        "city_qid": "Q420157",
        "city_name": "Pamukkale",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "entity_qid": "Q105893254",
        "entity_name": "Pamukkale Travertenleri",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Pamukkale",
        "lang": "tr",
    },
    {
        "text": "Bodrum, Muğla ilinde Ege kıyısında bir tatil beldesidir. Bodrum Kalesi, Sualtı Arkeoloji Müzesi'ne ev sahipliği yapar. Gece hayatı, marinalar ve mavi yolculuk turlarıyla tanınır.",
        "city_qid": "Q172267",
        "city_name": "Bodrum",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "overview",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Bodrum",
        "lang": "tr",
    },
    {
        "text": "Sümela Manastırı, Trabzon'un Maçka ilçesinde dik bir yamaçta inşa edilmiş tarihi bir manastırdır. MS 386 yılında kurulmuştur. Altındere Vadisi Milli Parkı içinde yer alır.",
        "city_qid": "Q45301",
        "city_name": "Trabzon",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Trabzon",
        "lang": "tr",
    },
    {
        "text": "Fethiye'deki Ölüdeniz, turkuaz renkli suları ve beyaz kumsalıyla Türkiye'nin en fotoğraflanan plajlarından biridir. Babadağ'dan yamaç paraşütü yapılabilir. Kelebek Vadisi tekneyle ulaşılabilen doğal bir koyudur.",
        "city_qid": "Q207998",
        "city_name": "Fethiye",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "activity",
        "source": "wikivoyage",
        "source_url": "https://tr.wikivoyage.org/wiki/Fethiye",
        "lang": "tr",
    },
]


def make_chunk_id(chunk: dict, index: int) -> str:
    """Deterministic UUID from chunk metadata."""
    key = f"{chunk['city_qid']}:{chunk['section']}:{index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------


async def get_embedding(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": OLLAMA_EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]


async def seed_qdrant() -> list[dict]:
    """Seed Qdrant and return chunk records with ids for Neo4j linking."""
    print("--- Seeding Qdrant ---")
    client = QdrantClient(url=QDRANT_URL)

    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION in collections:
        client.delete_collection(QDRANT_COLLECTION)
        print(f"  Deleted existing collection: {QDRANT_COLLECTION}")

    # Get vector dimension from first embedding
    print("  Detecting vector dimension...")
    first_embedding = await get_embedding(CHUNKS[0]["text"])
    dim = len(first_embedding)
    print(f"  Vector dimension: {dim}")

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"  Created collection: {QDRANT_COLLECTION}")

    points = []
    chunk_records = []

    for i, chunk in enumerate(CHUNKS):
        chunk_id = make_chunk_id(chunk, i)
        print(f"  [{i+1}/{len(CHUNKS)}] {chunk['city_name']} / {chunk['section']}")

        if i == 0:
            embedding = first_embedding
        else:
            embedding = await get_embedding(chunk["text"])

        payload = {
            "id": chunk_id,
            "text": chunk["text"],
            "city_qid": chunk["city_qid"],
            "city_name": chunk["city_name"],
            "country_qid": chunk["country_qid"],
            "country_name": chunk["country_name"],
            "section": chunk["section"],
            "source": chunk["source"],
            "source_url": chunk["source_url"],
            "lang": chunk["lang"],
        }

        points.append(PointStruct(id=chunk_id, vector=embedding, payload=payload))
        chunk_records.append({"chunk_id": chunk_id, "city_qid": chunk["city_qid"]})

    client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    print(f"  Inserted {len(points)} chunks")
    return chunk_records


# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------

NEO4J_CONSTRAINTS = """
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Country) REQUIRE c.qid IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (ci:City) REQUIRE ci.qid IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (a:Attraction) REQUIRE a.qid IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (act:Activity) REQUIRE act.qid IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (sc:SourceChunk) REQUIRE sc.chunk_id IS UNIQUE;
"""

NEO4J_SEED = """
// Country
MERGE (tr:Country {qid: 'Q43'})
SET tr.name = 'Türkiye', tr.continent = 'Asya/Avrupa', tr.currency = 'TRY', tr.language = 'Türkçe'

// Cities
MERGE (antalya:City {qid: 'Q6487'})
SET antalya.name = 'Antalya', antalya.population = 2548308,
    antalya.lat = 36.8969, antalya.lon = 30.7133,
    antalya.description = 'Akdeniz kıyısında turizm başkenti'
MERGE (istanbul:City {qid: 'Q406'})
SET istanbul.name = 'İstanbul', istanbul.population = 15840900,
    istanbul.lat = 41.0082, istanbul.lon = 28.9784,
    istanbul.description = 'İki kıtaya yayılan tarihi metropol'
MERGE (goreme:City {qid: 'Q6710593'})
SET goreme.name = 'Göreme', goreme.population = 2500,
    goreme.lat = 38.6431, goreme.lon = 34.8283,
    goreme.description = 'Kapadokya bölgesinin merkezi'
MERGE (bodrum:City {qid: 'Q172267'})
SET bodrum.name = 'Bodrum', bodrum.population = 196000,
    bodrum.lat = 37.0344, bodrum.lon = 27.4305,
    bodrum.description = 'Ege kıyısında tatil beldesi'
MERGE (fethiye:City {qid: 'Q207998'})
SET fethiye.name = 'Fethiye', fethiye.population = 162000,
    fethiye.lat = 36.6510, fethiye.lon = 29.1257,
    fethiye.description = 'Ölüdeniz ve kelebek vadisi'
MERGE (trabzon:City {qid: 'Q45301'})
SET trabzon.name = 'Trabzon', trabzon.population = 808000,
    trabzon.lat = 41.0027, trabzon.lon = 39.7168,
    trabzon.description = 'Karadeniz kıyısında tarihi liman şehri'
MERGE (selcuk:City {qid: 'Q876176'})
SET selcuk.name = 'Selçuk', selcuk.population = 36000,
    selcuk.lat = 37.9510, selcuk.lon = 27.3689,
    selcuk.description = 'Efes antik kentine ev sahipliği yapar'
MERGE (side:City {qid: 'Q152405'})
SET side.name = 'Side', side.population = 25000,
    side.lat = 36.7674, side.lon = 31.3901,
    side.description = 'Antik liman kenti ve sahil kasabası'
MERGE (pamukkale:City {qid: 'Q420157'})
SET pamukkale.name = 'Pamukkale', pamukkale.population = 15000,
    pamukkale.lat = 37.9204, pamukkale.lon = 29.1187,
    pamukkale.description = 'Beyaz travertenler ve Hierapolis'

// City -> Country
MERGE (antalya)-[:IN]->(tr)
MERGE (istanbul)-[:IN]->(tr)
MERGE (goreme)-[:IN]->(tr)
MERGE (bodrum)-[:IN]->(tr)
MERGE (fethiye)-[:IN]->(tr)
MERGE (trabzon)-[:IN]->(tr)
MERGE (selcuk)-[:IN]->(tr)
MERGE (side)-[:IN]->(tr)
MERGE (pamukkale)-[:IN]->(tr)

// Seasons
MERGE (yaz:Season {name: 'Yaz', months: 'Haziran-Ağustos'})
MERGE (ilkbahar:Season {name: 'İlkbahar', months: 'Mart-Mayıs'})
MERGE (sonbahar:Season {name: 'Sonbahar', months: 'Eylül-Kasım'})

MERGE (antalya)-[:BEST_SEASON]->(yaz)
MERGE (istanbul)-[:BEST_SEASON]->(ilkbahar)
MERGE (goreme)-[:BEST_SEASON]->(ilkbahar)
MERGE (bodrum)-[:BEST_SEASON]->(yaz)
MERGE (fethiye)-[:BEST_SEASON]->(yaz)
MERGE (trabzon)-[:BEST_SEASON]->(yaz)
MERGE (selcuk)-[:BEST_SEASON]->(ilkbahar)
MERGE (side)-[:BEST_SEASON]->(yaz)
MERGE (pamukkale)-[:BEST_SEASON]->(ilkbahar)

// Budget levels
MERGE (budget:Budget {level: 'budget'})
MERGE (mid:Budget {level: 'mid'})
MERGE (luxury:Budget {level: 'luxury'})

MERGE (antalya)-[:BUDGET_LEVEL]->(mid)
MERGE (istanbul)-[:BUDGET_LEVEL]->(mid)
MERGE (goreme)-[:BUDGET_LEVEL]->(mid)
MERGE (bodrum)-[:BUDGET_LEVEL]->(luxury)
MERGE (fethiye)-[:BUDGET_LEVEL]->(budget)
MERGE (trabzon)-[:BUDGET_LEVEL]->(budget)
MERGE (selcuk)-[:BUDGET_LEVEL]->(budget)
MERGE (side)-[:BUDGET_LEVEL]->(budget)
MERGE (pamukkale)-[:BUDGET_LEVEL]->(budget)

// City connections
MERGE (istanbul)-[:CONNECTED_TO {mode: 'flight', duration: '1h', cost_level: 'mid'}]->(antalya)
MERGE (antalya)-[:CONNECTED_TO {mode: 'bus', duration: '3h', cost_level: 'budget'}]->(fethiye)
MERGE (fethiye)-[:CONNECTED_TO {mode: 'bus', duration: '3h', cost_level: 'budget'}]->(bodrum)
MERGE (istanbul)-[:CONNECTED_TO {mode: 'flight', duration: '1.5h', cost_level: 'mid'}]->(trabzon)
MERGE (istanbul)-[:CONNECTED_TO {mode: 'flight', duration: '1h', cost_level: 'mid'}]->(goreme)
MERGE (antalya)-[:CONNECTED_TO {mode: 'bus', duration: '1.5h', cost_level: 'budget'}]->(side)

// Attractions
MERGE (kaleici:Attraction {qid: 'Q1424175'})
SET kaleici.name = 'Kaleiçi', kaleici.type = 'historic_district', kaleici.unesco = false,
    kaleici.description = 'Roma, Bizans ve Osmanlı dönemlerinden kalma tarihi merkez'
MERGE (kaleici)-[:LOCATED_IN]->(antalya)

MERGE (efes:Attraction {qid: 'Q80547'})
SET efes.name = 'Efes Antik Kenti', efes.type = 'ancient_city', efes.unesco = true,
    efes.description = 'Roma dönemi metropolü, Celsus Kütüphanesi'
MERGE (efes)-[:LOCATED_IN]->(selcuk)

MERGE (goreme_museum:Attraction {qid: 'Q1539966'})
SET goreme_museum.name = 'Göreme Açık Hava Müzesi', goreme_museum.type = 'museum',
    goreme_museum.unesco = true,
    goreme_museum.description = 'Kaya oyma kiliseler ve freskler'
MERGE (goreme_museum)-[:LOCATED_IN]->(goreme)

MERGE (pamukkale_attr:Attraction {qid: 'Q105893254'})
SET pamukkale_attr.name = 'Pamukkale Travertenleri', pamukkale_attr.type = 'natural',
    pamukkale_attr.unesco = true,
    pamukkale_attr.description = 'Beyaz kireçtaşı teraslar ve termal sular'
MERGE (pamukkale_attr)-[:LOCATED_IN]->(pamukkale)

MERGE (sumela:Attraction {qid: 'Q752484'})
SET sumela.name = 'Sümela Manastırı', sumela.type = 'monastery', sumela.unesco = false,
    sumela.description = 'Dik yamaçta inşa edilmiş MS 386 tarihli manastır'
MERGE (sumela)-[:LOCATED_IN]->(trabzon)

MERGE (bodrum_castle:Attraction {qid: 'Q672498'})
SET bodrum_castle.name = 'Bodrum Kalesi', bodrum_castle.type = 'castle',
    bodrum_castle.unesco = false,
    bodrum_castle.description = 'Sualtı Arkeoloji Müzesi, Haçlılar dönemi'
MERGE (bodrum_castle)-[:LOCATED_IN]->(bodrum)

// Activities
MERGE (balon:Activity {qid: 'ACT_BALON'})
SET balon.name = 'Sıcak Hava Balonu', balon.category = 'adventure',
    balon.season_dependent = true
MERGE (balon)-[:LOCATED_IN]->(goreme)

MERGE (parasut:Activity {qid: 'ACT_PARASUT'})
SET parasut.name = 'Yamaç Paraşütü', parasut.category = 'adventure',
    parasut.season_dependent = true
MERGE (parasut)-[:LOCATED_IN]->(fethiye)

MERGE (bogaz_turu:Activity {qid: 'ACT_BOGAZ'})
SET bogaz_turu.name = 'Boğaz Turu', bogaz_turu.category = 'cruise',
    bogaz_turu.season_dependent = false
MERGE (bogaz_turu)-[:LOCATED_IN]->(istanbul)

MERGE (mavi_yolculuk:Activity {qid: 'ACT_MAVI'})
SET mavi_yolculuk.name = 'Mavi Yolculuk', mavi_yolculuk.category = 'cruise',
    mavi_yolculuk.season_dependent = true
MERGE (mavi_yolculuk)-[:LOCATED_IN]->(bodrum)
"""


def seed_neo4j(chunk_records: list[dict]):
    print("\n--- Seeding Neo4j ---")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        # Clear
        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # Constraints
        for stmt in NEO4J_CONSTRAINTS.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                session.run(stmt)
        print("  Created unique constraints")

        # Entities and relationships
        session.run(NEO4J_SEED)
        print("  Inserted entities and relationships")

        # SourceChunk nodes — bridge between Qdrant and Neo4j
        for record in chunk_records:
            session.run(
                """
                MERGE (sc:SourceChunk {chunk_id: $chunk_id})
                WITH sc
                MATCH (city:City {qid: $city_qid})
                MERGE (sc)-[:ABOUT]->(city)
                """,
                chunk_id=record["chunk_id"],
                city_qid=record["city_qid"],
            )
        print(f"  Created {len(chunk_records)} SourceChunk nodes linked to cities")

        # Verify
        result = session.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label"
        )
        print("  Node counts:")
        for r in result:
            print(f"    {r['label']}: {r['count']}")

    driver.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    if not ALLOW_DB_RESET:
        print("ERROR: Set ALLOW_DB_RESET=true to run this script (destructive operation)")
        sys.exit(1)

    print("Tourism GraphRAG — Seed Data\n")
    chunk_records = await seed_qdrant()
    seed_neo4j(chunk_records)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
