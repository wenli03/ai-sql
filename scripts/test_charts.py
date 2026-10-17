import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wren-insight"))

from app import store  # noqa: E402
from app.nl2sql.pipeline import ask  # noqa: E402

QUESTIONS = [
    "总销售额是多少",
    "各品类销售额占比",
    "不同客户等级的客单价对比",
    "各渠道退款率对比",
    "销售额和订单数有什么关系",
    "最近3个月各渠道的销售额趋势",
    "销售额最高的10个商品",
]

if __name__ == "__main__":
    conn = store.get_connection("demo-postgres")
    questions = sys.argv[1:] or QUESTIONS
    for question in questions:
        print("\n" + "=" * 90)
        print("Q:", question)
        try:
            answer = ask(conn, question)
            print(
                "CHART:", answer.chart.type,
                "| x:", answer.chart.x,
                "| y:", answer.chart.y,
                "| series:", answer.chart.series,
            )
            print("ROWS:", answer.row_count, "ATTEMPTS:", answer.attempts, "MS:", answer.elapsed_ms)
            print("EXPLAIN:", answer.explanation)
            print("SQL:", answer.sql)
            print("WARN:", answer.warnings)
            if answer.rows:
                print("SAMPLE:", json.dumps(answer.rows[:3], ensure_ascii=False, default=str))
        except Exception as exc:
            print("ERROR:", type(exc).__name__, exc)
