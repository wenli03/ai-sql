"""Tests for `app.core.trace` — the instrumentation contract the benchmark
depends on. If these break, the benchmark numbers are no longer comparable.
"""

from __future__ import annotations

import time

import pytest

from app.core.trace import (
    ALL_SPANS,
    CONTEXT_SPANS,
    SPAN_CHART,
    SPAN_LOAD_SCHEMA,
    SPAN_LLM,
    STATUS_ERROR,
    STATUS_OK,
    Trace,
    percentile,
)
from app.models import AskResponse, TraceSpan


def test_span_records_elapsed_and_order():
    trace = Trace()
    with trace.span("a"):
        time.sleep(0.001)
    with trace.span("b"):
        pass
    assert [s.name for s in trace.spans] == ["a", "b"]
    assert trace.spans[0].ms >= 1.0
    assert trace.spans[0].status == STATUS_OK


def test_span_records_failure_and_reraises():
    trace = Trace()
    with pytest.raises(ValueError, match="boom"):
        with trace.span("x"):
            raise ValueError("boom")
    assert trace.spans[0].status == STATUS_ERROR
    assert "boom" in trace.spans[0].meta["error"]


def test_aggregate_rolls_up_repeated_spans():
    trace = Trace()
    for ms in (10.0, 30.0, 20.0):
        trace.record(SPAN_LLM, ms)
    bucket = trace.aggregate()[SPAN_LLM]
    assert bucket["count"] == 3
    assert bucket["ms"] == pytest.approx(60.0)
    assert bucket["ms_max"] == pytest.approx(30.0)
    assert bucket["ms_min"] == pytest.approx(10.0)
    assert bucket["errors"] == 0


def test_aggregate_counts_errors():
    trace = Trace()
    trace.record(SPAN_CHART, 5.0)
    with pytest.raises(RuntimeError):
        with trace.span(SPAN_CHART):
            raise RuntimeError("nope")
    assert trace.aggregate()[SPAN_CHART]["errors"] == 1


def test_context_ms_only_sums_context_spans():
    trace = Trace()
    for name in CONTEXT_SPANS:
        trace.record(name, 10.0)
    trace.record(SPAN_LLM, 999.0)
    assert trace.context_ms() == pytest.approx(10.0 * len(CONTEXT_SPANS))
    assert SPAN_LLM not in CONTEXT_SPANS


def test_total_ms_and_count_helpers():
    trace = Trace()
    trace.record(SPAN_LOAD_SCHEMA, 4.0)
    trace.record(SPAN_LOAD_SCHEMA, 6.0)
    assert trace.total_ms(SPAN_LOAD_SCHEMA) == pytest.approx(10.0)
    assert trace.count(SPAN_LOAD_SCHEMA) == 2


def test_wall_ms_increases():
    trace = Trace()
    time.sleep(0.002)
    assert trace.wall_ms >= 2.0


def test_to_dict_shape():
    trace = Trace()
    trace.record(SPAN_CHART, 1.5, chart="bar")
    payload = trace.to_dict()
    assert payload["name"] == "ask"
    assert payload["context_ms"] == 0.0
    assert payload["spans"][0]["name"] == SPAN_CHART
    assert payload["spans"][0]["meta"]["chart"] == "bar"
    assert SPAN_CHART in payload["aggregate"]


def test_percentile_is_nearest_rank():
    values = [float(i) for i in range(1, 21)]
    assert percentile(values, 50) == 10.0
    assert percentile(values, 95) == 19.0
    assert percentile(values, 100) == 20.0
    assert percentile([], 50) == 0.0


def test_span_serialises_into_api_model():
    trace = Trace()
    trace.record(SPAN_CHART, 2.0)
    span = TraceSpan.model_validate(trace.spans[0].to_dict())
    assert span.name == SPAN_CHART
    assert span.ms == pytest.approx(2.0)


def test_ask_response_trace_defaults_to_empty():
    payload = AskResponse(
        question="q", title="t", sql="SELECT 1", chart={"type": "table", "title": "t"}
    )
    assert payload.trace == []
    assert payload.trace_aggregate == {}
    assert payload.context_ms == 0.0


def test_span_names_are_unique():
    assert len(set(ALL_SPANS)) == len(ALL_SPANS)
