"""Seed the local Qdrant vector database with the same knowledge documents
used by the Chroma demo."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "wren-insight"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.vector.embeddings import EmbeddingClient  # noqa: E402
from seed_chroma import build_documents  # noqa: E402

QDRANT = "http://127.0.0.1:6333"
COLLECTION = "knowledge_docs"


def main() -> None:
    docs = build_documents()
    embedder = EmbeddingClient()
    vectors = embedder.embed([doc["title"] + "\n" + doc["content"] for doc in docs])
    with httpx.Client(base_url=QDRANT, timeout=120) as client:
        client.delete(f"/collections/{COLLECTION}")
        response = client.put(
            f"/collections/{COLLECTION}",
            json={"vectors": {"size": len(vectors[0]), "distance": "Cosine"}},
        )
        response.raise_for_status()
        for start in range(0, len(docs), 20):
            batch = docs[start:start + 20]
            points = []
            for index, doc in enumerate(batch, start=start + 1):
                points.append({
                    "id": index,
                    "vector": vectors[index - 1],
                    "payload": {
                        "title": doc["title"],
                        "category": doc["category"],
                        "topic": doc["topic"],
                        "source": doc["source"],
                        "language": doc["language"],
                        "priority": doc["priority"],
                        "created_at": doc["created_at"],
                        "document": doc["content"],
                    },
                })
            response = client.put(
                f"/collections/{COLLECTION}/points?wait=true",
                json={"points": points},
            )
            response.raise_for_status()
            print(f"upserted {min(start + 20, len(docs))}/{len(docs)}")
        info = client.get(f"/collections/{COLLECTION}").json()
        print("points_count:", (info.get("result") or {}).get("points_count"))
    print(json.dumps({"collection": COLLECTION, "count": len(docs), "dimension": len(vectors[0])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
