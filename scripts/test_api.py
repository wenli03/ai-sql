import json
import time

import httpx

BASE = "http://127.0.0.1:8000"


def wait_ready(timeout: float = 30) -> None:
    started = time.time()
    while time.time() - started < timeout:
        try:
            response = httpx.get(f"{BASE}/api/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("server not ready")


def main() -> None:
    wait_ready()
    with httpx.Client(base_url=BASE, timeout=180) as client:
        health = client.get("/api/health").json()
        print("HEALTH:", json.dumps(health, ensure_ascii=False))

        connections = client.get("/api/connections").json()
        print("CONNECTIONS:", [(c["id"], c["name"]) for c in connections])

        summary = client.get("/api/connections/demo-postgres/summary").json()
        print("SUMMARY:", {k: summary.get(k) for k in ("built", "domain", "dialect")})
        print("TABLES:", summary.get("tables"))

        payload = {
            "connection_id": "demo-postgres",
            "question": "最近6个月每月的销售额趋势",
            "max_rows": 500,
        }
        response = client.post("/api/ask", json=payload)
        print("ASK STATUS:", response.status_code)
        if response.status_code != 200:
            print("ASK ERROR:", response.text[:2000])
            return
        answer = response.json()
        print(
            "ANSWER:",
            answer["title"],
            "| chart:", answer["chart"]["type"],
            "| rows:", answer["row_count"],
            "| attempts:", answer["attempts"],
            "| provider:", answer["provider"],
            "| ms:", answer["elapsed_ms"],
        )
        print("SQL:", answer["sql"])
        print("ROWS:", json.dumps(answer["rows"][:2], ensure_ascii=False))
        print("CHART OPTION KEYS:", list((answer["chart"]["option"] or {}).keys()))

        # Chart switch
        rechart = client.post(
            "/api/rechart",
            json={
                "rows": answer["rows"],
                "columns": answer["columns"],
                "chart_type": "bar",
                "question": answer["question"],
                "title": answer["title"],
            },
        )
        print("RECHART:", rechart.status_code, rechart.json().get("type"))

        # Report flow
        report = client.post(
            "/api/reports",
            json={"connection_id": "demo-postgres", "title": "API 测试报告"},
        ).json()
        print("REPORT CREATED:", report["id"])
        added = client.post(f"/api/reports/{report['id']}/blocks", json={"answer": answer})
        print("BLOCK ADDED:", added.status_code, len(added.json().get("blocks", [])))
        exported = client.post(f"/api/reports/{report['id']}/export")
        print("EXPORT:", exported.status_code, exported.json().get("url"))
        html = client.get(f"/api/reports/{report['id']}/html")
        print("HTML:", html.status_code, len(html.text), "echarts:", "echarts" in html.text)
        reports = client.get("/api/reports").json()
        print("REPORTS:", [(r["id"], r["title"], r["block_count"]) for r in reports])

        # Keep the test idempotent.
        deleted = client.delete(f"/api/reports/{report['id']}")
        print("CLEANUP:", deleted.status_code)


if __name__ == "__main__":
    main()
