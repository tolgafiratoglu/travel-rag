"""Seed script for Qdrant (legacy demo chunks).

Neo4j is populated by scripts/ingest-wiki-neo4j.py, not this file.

Usage:
    ALLOW_DB_RESET=true QDRANT_URL=http://localhost:6333 \\
    OLLAMA_BASE_URL=http://localhost:11434 \\
    python scripts/seed.py
"""

import asyncio
import os
import sys
import uuid

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ALLOW_DB_RESET = os.getenv("ALLOW_DB_RESET", "false").lower() == "true"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "travel_chunks")

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
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Antalya",
        "lang": "tr",
    },
    {
        "text": "Kaleiçi, Antalya'nın tarihi merkezi olup Roma, Bizans ve Osmanlı dönemlerinden kalma yapıları barındırır. Hadrian Kapısı MS 130 yılında inşa edilmiştir. Bölge UNESCO Dünya Mirası geçici listesindedir.",
        "city_qid": "Q6487",
        "city_name": "Antalya",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Antalya",
        "lang": "tr",
    },
    {
        "text": "Side antik kenti, Antalya'ya 75 km uzaklıkta bir sahil kasabasıdır. Roma dönemi tiyatrosu 15.000 kişi kapasitelidir. Apollon Tapınağı gün batımı manzarasıyla ünlüdür.",
        "city_qid": "Q152405",
        "city_name": "Side",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Side,_Turkey",
        "lang": "tr",
    },
    {
        "text": "Kapadokya, Türkiye'nin Nevşehir ilinde yer alan volkanik bir bölgedir. Peri bacaları, yeraltı şehirleri ve sıcak hava balonu turlarıyla tanınır. Göreme Açık Hava Müzesi UNESCO Dünya Mirası listesindedir.",
        "city_qid": "Q6710593",
        "city_name": "Göreme",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "overview",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Cappadocia",
        "lang": "tr",
    },
    {
        "text": "İstanbul Boğazı, Asya ve Avrupa kıtalarını ayıran doğal bir su yoludur. Boğaz turu tekneleri Eminönü'nden kalkar. Kız Kulesi ve Rumeli Hisarı rotanın önemli noktalarıdır.",
        "city_qid": "Q406",
        "city_name": "İstanbul",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "activity",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Istanbul",
        "lang": "tr",
    },
    {
        "text": "Efes antik kenti, İzmir'in Selçuk ilçesinde yer alır. Celsus Kütüphanesi ve Büyük Tiyatro en bilinen yapılardır. Roma döneminde 250.000 nüfuslu bir metropoldü. UNESCO Dünya Mirası listesindedir.",
        "city_qid": "Q876176",
        "city_name": "Selçuk",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Sel%C3%A7uk",
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
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Pamukkale",
        "lang": "tr",
    },
    {
        "text": "Bodrum, Muğla ilinde Ege kıyısında bir tatil beldesidir. Bodrum Kalesi, Sualtı Arkeoloji Müzesi'ne ev sahipliği yapar. Gece hayatı, marinalar ve mavi yolculuk turlarıyla tanınır.",
        "city_qid": "Q172267",
        "city_name": "Bodrum",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "overview",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Bodrum",
        "lang": "tr",
    },
    {
        "text": "Sümela Manastırı, Trabzon'un Maçka ilçesinde dik bir yamaçta inşa edilmiş tarihi bir manastırdır. MS 386 yılında kurulmuştur. Altındere Vadisi Milli Parkı içinde yer alır.",
        "city_qid": "Q45301",
        "city_name": "Trabzon",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "attraction",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Trabzon",
        "lang": "tr",
    },
    {
        "text": "Fethiye'deki Ölüdeniz, turkuaz renkli suları ve beyaz kumsalıyla Türkiye'nin en fotoğraflanan plajlarından biridir. Babadağ'dan yamaç paraşütü yapılabilir. Kelebek Vadisi tekneyle ulaşılabilen doğal bir koyudur.",
        "city_qid": "Q207998",
        "city_name": "Fethiye",
        "country_qid": "Q43",
        "country_name": "Türkiye",
        "section": "activity",
        "source": "wikipedia",
        "source_url": "https://en.wikipedia.org/wiki/Fethiye",
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


async def seed_qdrant() -> None:
    print("--- Seeding Qdrant ---")
    client = QdrantClient(url=QDRANT_URL)

    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION in collections:
        client.delete_collection(QDRANT_COLLECTION)
        print(f"  Deleted existing collection: {QDRANT_COLLECTION}")

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

    client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    print(f"  Inserted {len(points)} chunks")


async def main():
    if not ALLOW_DB_RESET:
        print("ERROR: Set ALLOW_DB_RESET=true to run this script (destructive operation)")
        sys.exit(1)

    print("Travel RAG — Seed Qdrant\n")
    await seed_qdrant()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
