"""Seed the local Chroma vector database used by the demo connection.

Requires the Chroma container from docker-compose.vector.yml (port 8001) and
Ollama with nomic-embed-text (768 dimensions).
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "wren-insight"))

from app.vector.embeddings import EmbeddingClient  # noqa: E402

CHROMA = "http://127.0.0.1:8001"
TENANT = "default_tenant"
DATABASE = "default_database"
COLLECTION = "knowledge_docs"
BASE = f"/api/v2/tenants/{TENANT}/databases/{DATABASE}"

random.seed(20261017)

CATEGORIES = {
    "技术": ["API 接入", "部署运维", "数据同步", "权限模型", "性能优化"],
    "产品": ["会员体系", "促销活动", "搜索推荐", "移动端", "开放平台"],
    "售后": ["退款政策", "换货流程", "发票开具", "物流异常", "客服工单"],
    "财务": ["对账规则", "结算周期", "费用报销", "预算管理", "税务合规"],
    "人事": ["入职流程", "考勤制度", "绩效评估", "培训体系", "假期政策"],
    "运营": ["内容审核", "活动运营", "用户增长", "渠道合作", "数据看板"],
}
SOURCES = ["wiki", "confluence", "jira", "feishu"]
LANGUAGES = ["zh", "en"]


def _client() -> httpx.Client:
    return httpx.Client(base_url=CHROMA, timeout=120)


def ensure_collection() -> str:
    with _client() as client:
        collections = client.get(BASE + "/collections").json()
        for item in collections if isinstance(collections, list) else []:
            if item.get("name") == COLLECTION:
                print("reusing existing collection", item["id"])
                return item["id"]
        last_status = None
        for attempt in range(10):
            response = client.post(
                BASE + "/collections",
                json={"name": COLLECTION, "configuration": {"hnsw": {"space": "cosine"}}},
            )
            if response.status_code == 409:
                last_status = response.status_code
                # The collection may have been created concurrently or the list
                # endpoint may be briefly stale; re-list before giving up.
                time.sleep(1.0)
                collections = client.get(BASE + "/collections").json()
                for item in collections if isinstance(collections, list) else []:
                    if item.get("name") == COLLECTION:
                        print("reusing existing collection", item["id"])
                        return item["id"]
                continue
            response.raise_for_status()
            data = response.json()
            print("created collection", data["id"])
            return data["id"]
        raise RuntimeError(f"could not create Chroma collection (last status {last_status})")


def build_documents() -> list[dict]:
    docs = []
    created_base = datetime(2026, 4, 1)
    for index in range(120):
        category = random.choice(list(CATEGORIES))
        topic = random.choice(CATEGORIES[category])
        source = random.choice(SOURCES)
        language = random.choice(LANGUAGES)
        priority = random.choices([1, 2, 3, 4, 5], weights=[10, 20, 30, 25, 15])[0]
        days = random.randint(0, 190)
        created_at = (created_base + timedelta(days=days)).isoformat(timespec="seconds")
        if language == "zh":
            title = f"{topic}规范 V{random.randint(1, 5)}"
            content = (
                f"本文说明{topic}的标准流程、适用范围和常见问题。"
                f"涉及{category}团队、{source} 来源以及优先级 {priority} 的处理要求。"
                f"如遇到异常，请先查阅本规范，再联系对应负责人。"
            )
        else:
            title = f"{topic} Guideline V{random.randint(1, 5)}"
            content = (
                f"This document explains the standard process for {topic}, "
                f"including scope, exceptions, and escalation paths. "
                f"Owner: {category} team. Source: {source}. Priority: {priority}."
            )
        docs.append({
            "id": f"doc-{index + 1:03d}",
            "title": title,
            "category": category,
            "topic": topic,
            "source": source,
            "language": language,
            "priority": priority,
            "created_at": created_at,
            "content": content,
        })
    # Make the similarity demo unambiguous.
    docs[0].update({
        "title": "退款政策与售后处理规范",
        "category": "售后",
        "topic": "退款政策",
        "source": "wiki",
        "language": "zh",
        "priority": 5,
        "content": "退款政策说明：用户签收后 7 天内可申请无理由退款，退款审核通过后原路退回。"
                   "已发货订单需先确认物流状态，退款到账时间通常为 1-3 个工作日。",
    })
    docs[1].update({
        "title": "退货与换货流程",
        "category": "售后",
        "topic": "换货流程",
        "source": "confluence",
        "language": "zh",
        "priority": 4,
        "content": "退货与换货流程：用户提交售后单后，客服在 24 小时内审核；"
                   "退款、换货、补发分别走不同工单队列。",
    })
    return docs


def main() -> None:
    collection_id = ensure_collection()
    docs = build_documents()
    embedder = EmbeddingClient()
    vectors = embedder.embed([doc["title"] + "\n" + doc["content"] for doc in docs])
    if len(vectors) != len(docs):
        raise RuntimeError("embedding count mismatch")
    with _client() as client:
        for start in range(0, len(docs), 20):
            batch = docs[start:start + 20]
            payload = {
                "ids": [doc["id"] for doc in batch],
                "embeddings": vectors[start:start + 20],
                "metadatas": [
                    {
                        "title": doc["title"],
                        "category": doc["category"],
                        "topic": doc["topic"],
                        "source": doc["source"],
                        "language": doc["language"],
                        "priority": doc["priority"],
                        "created_at": doc["created_at"],
                    }
                    for doc in batch
                ],
                "documents": [doc["content"] for doc in batch],
            }
            response = client.post(BASE + f"/collections/{collection_id}/upsert", json=payload)
            response.raise_for_status()
            print(f"added {min(start + 20, len(docs))}/{len(docs)}")
        count = client.get(BASE + f"/collections/{collection_id}/count").json()
        print("collection count:", count)
    print(json.dumps({"collection": COLLECTION, "count": len(docs), "dimension": len(vectors[0])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
