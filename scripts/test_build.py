import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wren-insight"))

from app import store  # noqa: E402
from app.nl2sql.pipeline import _execute, validate_sql  # noqa: E402
from app.semantic import store as semantic_store  # noqa: E402


def run(connection_id: str) -> None:
    conn = store.get_connection(connection_id)
    print(f"\n===== {connection_id} ({conn.dialect}) =====")
    summary = semantic_store.build(conn)
    print("build:", summary)
    project = semantic_store.project_summary(connection_id)
    print("domain:", project.get("domain"))
    print("metrics:", [m["label"] for m in project.get("metrics", [])])
    print("dimensions:", [d["label"] for d in project.get("dimensions", [])])
    print("rules:", project.get("rules"))

    manifest = semantic_store.manifest(connection_id)
    b64 = semantic_store.manifest_b64(connection_id)
    print("manifest models:", [m["name"] for m in manifest.get("models", [])])
    print("manifest views:", [v["name"] for v in manifest.get("views", [])])

    queries = [
        (
            "status breakdown",
            'SELECT status, COUNT(*) AS order_count, ROUND(SUM(total_amount), 2) AS revenue '
            'FROM orders GROUP BY status ORDER BY revenue DESC',
        ),
        (
            "category via view",
            'SELECT category, ROUND(SUM(line_amount), 2) AS revenue, SUM(quantity) AS qty '
            'FROM sales_line GROUP BY category ORDER BY revenue DESC',
        ),
    ]
    for label, sql in queries:
        print(f"-- {label}")
        try:
            clean, warnings = validate_sql(sql, conn.dialect, manifest)
            df, planned = _execute(conn, b64, clean, 100)
            print(df.to_string(index=False))
            if warnings:
                print("warnings:", warnings)
        except Exception as exc:
            print("ERROR:", type(exc).__name__, exc)


if __name__ == "__main__":
    run("demo-postgres")
    run("demo-mysql")
