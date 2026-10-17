"""Contract tests for the benchmark fixtures.

The benchmark is only a yardstick if the yardstick itself cannot drift: these
tests pin the question count, the threshold schema and the environment probe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.trace import ALL_SPANS
from bench import env

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
QUESTIONS = BENCH_DIR / "questions.json"
THRESHOLDS = BENCH_DIR / "thresholds.json"

EXPECTED_QUESTION_COUNT = 20
KNOWN_TARGETS = {
    "p50_wall_ms_max",
    "p95_wall_ms_max",
    "p50_context_ms_max",
    "first_try_rate_min",
    "success_rate_min",
    "lineage_coverage_min",
    "first_screen_kb_max",
}
ALLOWED_CHARTS = {"line", "bar", "pie", "scatter", "kpi", "table"}


@pytest.fixture(scope="module")
def questions() -> dict:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def thresholds() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8"))


def test_question_set_size_and_uniqueness(questions):
    items = questions["questions"]
    assert len(items) == EXPECTED_QUESTION_COUNT
    ids = [item["id"] for item in items]
    texts = [item["text"] for item in items]
    assert len(set(ids)) == len(ids)
    assert len(set(texts)) == len(texts)


def test_question_fields_are_complete(questions):
    for item in questions["questions"]:
        assert item["id"]
        assert item["text"].strip()
        assert item["expect_chart"] in ALLOWED_CHARTS


def test_thresholds_schema(thresholds):
    assert thresholds["version"] >= 1
    assert isinstance(thresholds["baseline"], dict)
    assert set(thresholds["targets"]) == KNOWN_TARGETS


def test_only_first_screen_target_is_calibrated_before_baseline(thresholds):
    """Regression guard for §5-待补充: do not let un-measured gates in silently."""
    calibrated = {k: v for k, v in thresholds["targets"].items() if v is not None}
    assert calibrated == {"first_screen_kb_max": 350}


def test_benchmark_span_constants_exist():
    """runner.py reads spans by name; a renamed constant must fail here, not silently."""
    from bench import runner

    for name in (
        runner.SPAN_CHART,
        runner.SPAN_EXECUTE,
        runner.SPAN_LLM,
        runner.SPAN_LLM_HTTP,
        runner.SPAN_PROVENANCE,
        runner.SPAN_VALIDATE,
    ):
        assert name in ALL_SPANS


def test_summarise_handles_all_failed_samples():
    from bench.runner import summarize

    metrics = summarize([{"ok": False, "wall_ms": 10.0, "context_ms": 1.0, "stages": {},
                          "llm_calls": 0, "llm_http_calls": 0, "attempts": 0,
                          "lineage_coverage": None, "chart_match": False}])
    assert metrics["success_rate"] == 0.0
    assert metrics["p50_wall_ms"] == 0.0
    assert metrics["n_failed"] == 1


def test_summarise_computes_first_try_rate():
    from bench.runner import summarize

    samples = [
        {"ok": True, "wall_ms": 100.0, "context_ms": 10.0, "attempts": 1,
         "lineage_coverage": 1.0, "chart_match": True, "stages": {},
         "llm_calls": 1, "llm_http_calls": 1},
        {"ok": True, "wall_ms": 300.0, "context_ms": 10.0, "attempts": 3,
         "lineage_coverage": 0.5, "chart_match": False, "stages": {},
         "llm_calls": 3, "llm_http_calls": 3},
    ]
    metrics = summarize(samples)
    assert metrics["first_try_rate"] == pytest.approx(0.5)
    assert metrics["success_rate"] == 1.0
    # nearest-rank P50 over n=2 => rank 1 => the faster sample, not the mean
    assert metrics["p50_wall_ms"] == 100.0
    assert metrics["lineage_coverage_mean"] == pytest.approx(0.75)


def test_evaluate_skips_uncalibrated_targets():
    from bench.runner import evaluate

    metrics = {"p50_wall_ms": 100.0, "p95_wall_ms": 200.0, "p50_context_ms": 10.0,
               "first_try_rate": 0.9, "success_rate": 1.0, "lineage_coverage_mean": 0.95}
    result = {"env": {"web": {"first_screen_kb": 1090.0}},
              "thresholds": {"targets": {"p50_wall_ms_max": None,
                                         "first_screen_kb_max": 350}}}
    checks = {c["name"]: c for c in evaluate(metrics, result)}
    assert checks["p50_wall_ms_max"]["ok"] is None
    assert checks["first_screen_kb_max"]["ok"] is False


def test_env_first_screen_assets_present():
    web = env.web_assets()
    assert web["missing_first_screen_assets"] == []
    assert web["first_screen_bytes"] > 0
    assert web["first_screen_kb"] > 0


def test_env_snapshot_has_required_sections():
    snapshot = env.snapshot()
    assert {"python", "dependencies", "web", "rss_mb", "llm"} <= set(snapshot)
    assert snapshot["dependencies"]["distribution_count"] > 0
    assert snapshot["llm"]["model"]
