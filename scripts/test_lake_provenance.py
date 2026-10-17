import json
import urllib.request

BASE = "http://127.0.0.1:8000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, payload=None, timeout: int = 180):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with OPENER.open(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        try:
            return response.status, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return response.status, raw


def wait_ready(timeout=45):
    import time
    started = time.time()
    while time.time() - started < timeout:
        try:
            status, _ = call("GET", "/api/health", timeout=3)
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.6)
    raise RuntimeError("server not ready")


def main() -> None:
    wait_ready()
    status, answer = call("POST", "/api/ask", {"connection_id": "demo-duckdb", "question": "各品类销售额排行"})
    print("ask:", status)
    print(answer["title"], answer["chart"]["type"], answer["row_count"], answer["provider"], answer["elapsed_ms"])
    prov = answer["provenance"]
    print("score:", prov["reliability_score"])
    print("sources:", [(x["model"], x["catalog"], x["schema_name"], x["table"], x["location"]) for x in prov["source_tables"]])
    print("lineage:", [(e["output_column"], e["source_table"], e["source_column"]) for e in prov["lineage"]])
    print("freshness:", [(f["model"], f["row_count"], f["max_time_value"], f["note"]) for f in prov["freshness"]])
    print("checks:", [(c["name"], c["status"]) for c in prov["checks"]])

    status, fresh = call("POST", "/api/connections/demo-duckdb/freshness", {"models": ["order_items", "products"]})
    print("live freshness:", status, [(f["model"], f["row_count"], f["max_time_value"]) for f in fresh["freshness"]])

    status, report = call("POST", "/api/reports", {"connection_id": "demo-duckdb", "title": "湖仓溯源报告"})
    call("POST", f"/api/reports/{report['id']}/blocks", {"answer": answer})
    status, exported = call("POST", f"/api/reports/{report['id']}/export")
    print("export:", status, exported)
    status, html = call("GET", f"/api/reports/{report['id']}/html")
    print(
        "html provenance:",
        "数据溯源" in html,
        "| parquet:",
        "orders.parquet" in html or "order_items.parquet" in html,
        "| lineage:",
        "字段级血缘" in html,
        "| score:",
        str(prov["reliability_score"]) in html,
    )
    call("DELETE", f"/api/reports/{report['id']}")


if __name__ == "__main__":
    main()
