"""Baseline benchmark runner for the ask pipeline.

Runs the 20-question fixed set in-process (no HTTP server), records the
per-stage trace produced by `core.trace`, and writes a machine-readable result.

    python -m bench.runner --connection demo-postgres                # 测量
    python -m bench.runner --connection demo-postgres --update-baseline
    python -m bench.runner --connection demo-postgres --check        # 门禁
    python -m bench.runner --dry-run                                 # 只看环境与问题集

Design notes (keep these when refactoring):
- **In-process, not over HTTP.** The first target is pipeline latency; adding
  uvicorn/JSON noise would make the −40% goal unmeasurable. HTTP-level
  benchmarks come later, once the async rewrite lands.
- **Warmup is separated from measurement.** The first ask pays one-time lazy
  imports (`wren.engine`). It is reported as `cold_start_ms`, not averaged into
  P50, so steady-state regressions stay visible.
- **Every metric is derived from the trace**, never from a second timer, so the
  benchmark and the API response can never disagree.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
ROOT = BENCH_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402
from app.core.trace import (  # noqa: E402
    SPAN_CHART,
    SPAN_EXECUTE,
    SPAN_EXECUTE_DRY_PLAN,
    SPAN_EXECUTE_QUERY,
    SPAN_LLM,
    SPAN_LLM_HTTP,
    SPAN_PROVENANCE,
    SPAN_VALIDATE,
    Trace,
    percentile,
)
from app.nl2sql.pipeline import ask  # noqa: E402
from bench import env  # noqa: E402

DEFAULT_QUESTIONS = BENCH_DIR / "questions.json"
DEFAULT_THRESHOLDS = BENCH_DIR / "thresholds.json"
RESULTS_DIR = BENCH_DIR / "results"


def load_questions(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data["questions"]
    if limit:
        questions = questions[:limit]
    return questions


def _stage_ms(aggregate: dict[str, Any], name: str) -> float:
    """Read a stage duration from either a trace aggregate or a flat stage map.

    ``run_one`` stores ``stages`` as ``{name: ms}`` for compact JSON, while
    ``Trace.aggregate()`` uses ``{name: {ms, count, ...}}``. Accept both so the
    summariser works on recorded samples and on hand-written fixtures.
    """
    value = aggregate.get(name, 0.0)
    if isinstance(value, dict):
        value = value.get("ms", 0.0)
    return round(float(value or 0.0), 3)


def _stage_count(aggregate: dict[str, Any], name: str) -> int:
    value = aggregate.get(name, {})
    if isinstance(value, dict):
        return int(value.get("count", 0))
    return int(value or 0)


def run_one(conn: Any, item: dict[str, Any]) -> dict[str, Any]:
    trace = Trace(f"ask:{item['id']}")
    started = time.perf_counter()
    try:
        answer = ask(conn, item["text"], trace=trace)
    except Exception as exc:  # noqa: BLE001 - benchmark must survive failures
        return {
            "id": item["id"],
            "question": item["text"],
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "context_ms": round(trace.context_ms(), 3),
            "attempts": 0,
            "row_count": 0,
            "chart_type": None,
            "expect_chart": item.get("expect_chart"),
            "chart_match": False,
            "reliability_score": None,
            "lineage_coverage": None,
            "llm_calls": _stage_count(trace.aggregate(), SPAN_LLM),
            "llm_http_calls": _stage_count(trace.aggregate(), SPAN_LLM_HTTP),
            "stages": {k: v["ms"] for k, v in trace.aggregate().items()},
            "warnings": [],
        }

    aggregate = answer.trace_aggregate
    provenance = answer.provenance
    coverage: float | None = None
    if provenance is not None and answer.columns:
        mapped = {
            edge.output_column
            for edge in provenance.lineage
            if edge.output_column and edge.source_column
        }
        coverage = round(len(mapped) / len(answer.columns), 4)

    return {
        "id": item["id"],
        "question": item["text"],
        "ok": True,
        "error": None,
        "wall_ms": float(answer.elapsed_ms),
        "context_ms": float(answer.context_ms),
        "attempts": answer.attempts,
        "row_count": answer.row_count,
        "chart_type": answer.chart.type if answer.chart else None,
        "expect_chart": item.get("expect_chart"),
        "chart_match": bool(
            item.get("expect_chart") and answer.chart and answer.chart.type == item["expect_chart"]
        ),
        "reliability_score": provenance.reliability_score if provenance else None,
        "lineage_coverage": coverage,
        "llm_calls": _stage_count(aggregate, SPAN_LLM),
        "llm_http_calls": _stage_count(aggregate, SPAN_LLM_HTTP),
        "stages": {k: v["ms"] for k, v in aggregate.items()},
        "warnings": list(answer.warnings),
    }


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [s for s in samples if s["ok"]]
    walls = [s["wall_ms"] for s in ok]
    contexts = [s["context_ms"] for s in ok]
    executes = [
        _stage_ms(s["stages"], SPAN_EXECUTE)
        + _stage_ms(s["stages"], SPAN_EXECUTE_DRY_PLAN)
        + _stage_ms(s["stages"], SPAN_EXECUTE_QUERY)
        for s in ok
    ]
    charts = [_stage_ms(s["stages"], SPAN_CHART) for s in ok]
    provenances = [_stage_ms(s["stages"], SPAN_PROVENANCE) for s in ok]
    validations = [_stage_ms(s["stages"], SPAN_VALIDATE) for s in ok]
    coverages = [s["lineage_coverage"] for s in ok if s["lineage_coverage"] is not None]

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    return {
        "n": len(samples),
        "n_ok": len(ok),
        "n_failed": len(samples) - len(ok),
        "success_rate": round(len(ok) / len(samples), 4) if samples else 0.0,
        "first_try_rate": (
            round(sum(1 for s in ok if s["attempts"] == 1) / len(ok), 4) if ok else 0.0
        ),
        "chart_match_rate": (
            round(sum(1 for s in ok if s["chart_match"]) / len(ok), 4) if ok else 0.0
        ),
        "lineage_coverage_mean": round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
        "lineage_coverage_min": round(min(coverages), 4) if coverages else 0.0,
        "p50_wall_ms": round(percentile(walls, 50), 1),
        "p95_wall_ms": round(percentile(walls, 95), 1),
        "mean_wall_ms": mean(walls),
        "max_wall_ms": round(max(walls), 1) if walls else 0.0,
        "p50_context_ms": round(percentile(contexts, 50), 1),
        "p95_context_ms": round(percentile(contexts, 95), 1),
        "p50_execute_ms": round(percentile(executes, 50), 1),
        "p50_chart_ms": round(percentile(charts, 50), 1),
        "p50_provenance_ms": round(percentile(provenances, 50), 1),
        "p50_validate_ms": round(percentile(validations, 50), 1),
        "llm_calls_total": sum(s["llm_calls"] for s in samples),
        "llm_http_calls_total": sum(s["llm_http_calls"] for s in samples),
        "llm_http_calls_per_ask": (
            round(sum(s["llm_http_calls"] for s in samples) / len(samples), 2) if samples else 0.0
        ),
        "repair_asks": sum(1 for s in ok if s["attempts"] > 1),
    }


def evaluate(metrics: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare measured metrics against `thresholds.json` targets.

    A `null` target is skipped on purpose: an un-calibrated gate would either
    be arbitrary or block work that is not done yet.
    """
    env_web = result.get("env", {}).get("web", {})
    #: (threshold key, measured value, comparison) — "<=" means smaller is better.
    pairs: list[tuple[str, float | None, str]] = [
        ("p50_wall_ms_max", metrics["p50_wall_ms"], "<="),
        ("p95_wall_ms_max", metrics["p95_wall_ms"], "<="),
        ("p50_context_ms_max", metrics["p50_context_ms"], "<="),
        ("first_try_rate_min", metrics["first_try_rate"], ">="),
        ("success_rate_min", metrics["success_rate"], ">="),
        ("lineage_coverage_min", metrics["lineage_coverage_mean"], ">="),
        ("first_screen_kb_max", env_web.get("first_screen_kb"), "<="),
    ]
    targets = result.get("thresholds", {}).get("targets", {})
    checks: list[dict[str, Any]] = []
    for key, actual, op in pairs:
        target = targets.get(key)
        if target is None:
            checks.append({"name": key, "actual": actual, "target": None, "op": op, "ok": None})
            continue
        ok = actual is not None and (actual <= target if op == "<=" else actual >= target)
        checks.append({"name": key, "actual": actual, "target": target, "op": op, "ok": bool(ok)})
    return checks


def print_report(result: dict[str, Any]) -> None:
    metrics = result["metrics"]
    print("\n" + "=" * 78)
    print(f"数语 benchmark · connection={result['connection_id']} · tag={result['tag']}")
    print("=" * 78)
    print(f"{'id':<5}{'ok':<4}{'ms':>9}{'ctx':>8}{'att':>5}{'rows':>7}  chart")
    print("-" * 78)
    for sample in result["samples"]:
        print(
            f"{sample['id']:<5}"
            f"{('Y' if sample['ok'] else 'N'):<4}"
            f"{sample['wall_ms']:>9.0f}"
            f"{sample['context_ms']:>8.0f}"
            f"{sample['attempts']:>5}"
            f"{sample['row_count']:>7}  "
            f"{sample['chart_type'] or '-'}"
            + (f"  ERR {sample['error']}" if sample["error"] else "")
        )
    print("-" * 78)
    print(
        f"success={metrics['success_rate']:.0%}  "
        f"first_try={metrics['first_try_rate']:.0%}  "
        f"chart_match={metrics['chart_match_rate']:.0%}  "
        f"lineage={metrics['lineage_coverage_mean']:.0%}"
    )
    print(
        f"wall_ms   p50={metrics['p50_wall_ms']:.0f}  p95={metrics['p95_wall_ms']:.0f}  "
        f"mean={metrics['mean_wall_ms']:.0f}  max={metrics['max_wall_ms']:.0f}"
    )
    print(
        f"context   p50={metrics['p50_context_ms']:.0f}  p95={metrics['p95_context_ms']:.0f}"
        f"   execute p50={metrics['p50_execute_ms']:.0f}"
        f"   chart p50={metrics['p50_chart_ms']:.0f}"
        f"   provenance p50={metrics['p50_provenance_ms']:.0f}"
    )
    print(
        f"llm calls={metrics['llm_calls_total']}  http calls={metrics['llm_http_calls_total']}"
        f"  ({metrics['llm_http_calls_per_ask']} per ask)  repaired asks={metrics['repair_asks']}"
    )
    web = result["env"]["web"]
    print(
        f"env       first_screen={web['first_screen_kb']}KB  "
        f"deps={result['env']['dependencies']['distribution_count']}  "
        f"site-packages={result['env']['dependencies']['site_packages_mb']}MB  "
        f"rss={result['env'].get('rss_mb')}MB"
    )
    if result.get("cold_start_ms") is not None:
        print(f"cold_start(warmup, excluded from p50)={result['cold_start_ms']:.0f}ms")
    print("=" * 78)

    for check in result.get("checks", []):
        if check["ok"] is None:
            print(f"  SKIP  {check['name']:<24} actual={check['actual']} (target not calibrated)")
        else:
            flag = "PASS" if check["ok"] else "FAIL"
            print(
                f"  {flag}  {check['name']:<24} actual={check['actual']} "
                f"{check['op']} {check['target']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数语 ask-pipeline benchmark")
    parser.add_argument("--connection", default="demo-postgres")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--limit", type=int, default=None, help="only take the first N questions")
    parser.add_argument("--runs", type=int, default=1, help="repeat the set N times")
    parser.add_argument("--warmup", type=int, default=1, help="throwaway asks before measuring")
    parser.add_argument("--tag", default="baseline")
    parser.add_argument("--out", default=str(RESULTS_DIR))
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print env + question set only")
    args = parser.parse_args(argv)

    questions = load_questions(Path(args.questions), args.limit)
    environment = env.snapshot()

    if args.dry_run:
        print(json.dumps(
            {"questions": len(questions), "env": environment}, ensure_ascii=False, indent=2
        ))
        return 0

    conn = store.get_connection(args.connection)

    cold_start_ms: float | None = None
    for _ in range(args.warmup):
        warm = Trace("warmup")
        started = time.perf_counter()
        try:
            ask(conn, questions[0]["text"], trace=warm)
        except Exception as exc:  # noqa: BLE001
            print(f"warmup failed: {type(exc).__name__}: {exc}")
        cold_start_ms = round((time.perf_counter() - started) * 1000, 1)

    samples: list[dict[str, Any]] = []
    for run in range(args.runs):
        for item in questions:
            sample = run_one(conn, item)
            sample["run"] = run
            samples.append(sample)
            flag = "ok " if sample["ok"] else "ERR"
            print(f"[{flag}] {sample['id']} {sample['wall_ms']:.0f}ms  {item['text']}")
            if sample["error"]:
                print(f"       {sample['error']}")

    metrics = summarize(samples)
    thresholds = json.loads(DEFAULT_THRESHOLDS.read_text(encoding="utf-8"))
    result: dict[str, Any] = {
        "tag": args.tag,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "connection_id": args.connection,
        "runs": args.runs,
        "cold_start_ms": cold_start_ms,
        "env": environment,
        "samples": samples,
        "metrics": metrics,
        "thresholds": thresholds,
    }
    if args.check or args.update_baseline:
        result["checks"] = evaluate(metrics, result)

    print_report(result)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"{args.tag}-{args.connection}-{stamp}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult -> {out_path}")

    if args.update_baseline:
        thresholds["baseline"] = {
            "measured_at": result["generated_at"],
            "connection_id": args.connection,
            "p50_wall_ms": metrics["p50_wall_ms"],
            "p95_wall_ms": metrics["p95_wall_ms"],
            "p50_context_ms": metrics["p50_context_ms"],
            "first_try_rate": metrics["first_try_rate"],
            "success_rate": metrics["success_rate"],
            "lineage_coverage_mean": metrics["lineage_coverage_mean"],
            "first_screen_kb": environment["web"]["first_screen_kb"],
        }
        thresholds["updated_at"] = result["generated_at"]
        DEFAULT_THRESHOLDS.write_text(
            json.dumps(thresholds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline -> {DEFAULT_THRESHOLDS}")

    if args.check:
        failed = [c for c in result.get("checks", []) if c["ok"] is False]
        if failed:
            print(f"\n{len(failed)} threshold(s) failed.")
            return 1
        print("\nall calibrated thresholds passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
