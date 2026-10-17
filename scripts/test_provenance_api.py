import json
import urllib.request

BASE = "http://127.0.0.1:8000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, payload=None, timeout: int = 240):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with OPENER.open(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw) if raw else None


def main() -> None:
    for cid, question in [
        ("demo-postgres", "各渠道的订单数和客单价对比"),
        ("demo-mysql", "各品类销售额排行"),
    ]:
        status, answer = call("POST", "/api/ask", {"connection_id": cid, "question": question})
        print("\n", cid, status)
        if status != 200:
            print(answer)
            continue
        prov = answer.get("provenance") or {}
        print(answer["title"], answer["chart"]["type"], answer["row_count"], answer["provider"])
        print("score:", prov.get("reliability_score"))
        print("sources:", [(x["model"], x["catalog"], x["schema_name"], x["table"]) for x in prov.get("source_tables", [])])
        print("metrics:", [m["label"] for m in prov.get("metrics_used", [])])
        print("lineage:", [(e["output_column"], e["source_table"], e["source_column"]) for e in prov.get("lineage", [])])
        print("freshness:", [(f["model"], f["row_count"], f["max_time_value"]) for f in prov.get("freshness", [])])

    status, answer = call("POST", "/api/ask", {"connection_id": "demo-duckdb", "question": "各品类销售额排行"})
    status, report = call("POST", "/api/reports", {"connection_id": "demo-duckdb", "title": "湖仓溯源测试报告"})
    status, _ = call("POST", f"/api/reports/{report['id']}/blocks", {"answer": answer})
    status, exported = call("POST", f"/api/reports/{report['id']}/export")
    print("\nexport:", status, exported)
    request = urllib.request.Request(BASE + f"/api/reports/{report['id']}/html")
    with OPENER.open(request, timeout=60) as response:
        html = response.read().decode("utf-8")
    print("html contains provenance:", "数据溯源" in html, "| parquet:", "orders.parquet" in html, "| lineage:", "字段级血缘" in html)
    call("DELETE", f"/api/reports/{report['id']}")
    from pathlib import Path
    Path(f"wren-insight/data/reports/{report['id']}.html").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
