import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wren-insight"))

from app import store  # noqa: E402
from app.nl2sql.pipeline import ask  # noqa: E402

QUESTIONS = [
    "最近6个月每月的销售额趋势",
    "各品类销售额排行",
    "高价值客户有哪些",
    "各渠道的订单数和客单价对比",
    "各省份销售额分布",
]

if __name__ == "__main__":
    connection_id = sys.argv[1] if len(sys.argv) > 1 else "demo-postgres"
    questions = [sys.argv[2]] if len(sys.argv) > 2 else QUESTIONS
    conn = store.get_connection(connection_id)
    for question in questions:
        print("\n" + "=" * 90)
        print("Q:", question)
        try:
            answer = ask(conn, question)
            print("TITLE:", answer.title)
            print("CHART:", answer.chart.type, "x=", answer.chart.x, "y=", answer.chart.y)
            print("ROWS:", answer.row_count, "ATTEMPTS:", answer.attempts, "MS:", answer.elapsed_ms)
            print("EXPLAIN:", answer.explanation)
            print("SQL:", answer.sql)
            print("WARN:", answer.warnings)
            if answer.rows:
                print("SAMPLE:", json.dumps(answer.rows[:3], ensure_ascii=False, default=str))
        except Exception as exc:
            print("ERROR:", type(exc).__name__, exc)
            detail = getattr(exc, "detail", None)
            if detail:
                print("DETAIL:", detail)
