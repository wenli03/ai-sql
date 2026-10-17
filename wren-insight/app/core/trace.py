"""Structured timing instrumentation for the ask pipeline.

Design constraints (deliberate, do not "simplify" away):

- **No global state.** A `Trace` is an explicit object passed down the call
  chain. `bench/` runs many asks in one process and must not have spans leak
  between them, which a contextvar/global singleton would cause.
- **Span names are a contract.** `bench/runner.py` and `tests/test_trace.py`
  assert on these constants. Renaming one is a breaking change to the benchmark.
- **Instrumentation must never change behaviour.** `span()` records the failure
  and re-raises; it never swallows or converts exceptions.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

# --- Span name contract -----------------------------------------------------

SPAN_TOTAL = "total"
SPAN_ENSURE_BUILT = "semantic.ensure_built"
SPAN_LOAD_SCHEMA = "semantic.load_schema"
SPAN_LOAD_ONTOLOGY = "semantic.load_ontology"
SPAN_MANIFEST = "semantic.manifest"
SPAN_MANIFEST_B64 = "semantic.manifest_b64"
SPAN_PROMPT = "nl2sql.prompt"
SPAN_LLM = "nl2sql.llm"
SPAN_LLM_HTTP = "nl2sql.llm.http"
SPAN_VALIDATE = "nl2sql.validate"
SPAN_EXECUTE = "nl2sql.execute"
SPAN_EXECUTE_DRY_PLAN = "nl2sql.execute.dry_plan"
SPAN_EXECUTE_QUERY = "nl2sql.execute.query"
SPAN_CHART = "insight.chart"
SPAN_PROVENANCE = "insight.provenance"

#: Spans that belong to "load the semantic context before we can ask the LLM".
#: Kept as an explicit tuple so the benchmark can report
#: ``context = ensure + schema + ontology + manifest + manifest_b64 + prompt``
#: as one number — that sum is exactly the overhead the restart must eliminate.
CONTEXT_SPANS: tuple[str, ...] = (
    SPAN_ENSURE_BUILT,
    SPAN_LOAD_SCHEMA,
    SPAN_LOAD_ONTOLOGY,
    SPAN_MANIFEST,
    SPAN_MANIFEST_B64,
    SPAN_PROMPT,
)

ALL_SPANS: tuple[str, ...] = (
    SPAN_TOTAL,
    *CONTEXT_SPANS,
    SPAN_LLM,
    SPAN_LLM_HTTP,
    SPAN_VALIDATE,
    SPAN_EXECUTE,
    SPAN_EXECUTE_DRY_PLAN,
    SPAN_EXECUTE_QUERY,
    SPAN_CHART,
    SPAN_PROVENANCE,
)

STATUS_OK = "ok"
STATUS_ERROR = "error"


@dataclass
class Span:
    name: str
    ms: float
    status: str = STATUS_OK
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ms": round(self.ms, 3),
            "status": self.status,
            "meta": self.meta,
        }


class Trace:
    """Collects ordered spans for a single logical operation."""

    def __init__(self, name: str = "ask") -> None:
        self.name = name
        self.spans: list[Span] = []
        self._wall_started = time.perf_counter()

    @contextmanager
    def span(self, name: str, **meta: Any) -> Iterator[Span]:
        started = time.perf_counter()
        record = Span(name=name, ms=0.0, meta=dict(meta))
        self.spans.append(record)
        try:
            yield record
        except BaseException as exc:  # noqa: BLE001 - record then re-raise
            record.status = STATUS_ERROR
            record.meta["error"] = f"{type(exc).__name__}: {exc}"[:400]
            raise
        finally:
            record.ms = (time.perf_counter() - started) * 1000.0

    def record(self, name: str, ms: float, **meta: Any) -> Span:
        record = Span(name=name, ms=ms, meta=dict(meta))
        self.spans.append(record)
        return record

    @property
    def wall_ms(self) -> float:
        return (time.perf_counter() - self._wall_started) * 1000.0

    def total_ms(self, name: str) -> float:
        return sum(s.ms for s in self.spans if s.name == name)

    def count(self, name: str) -> int:
        return sum(1 for s in self.spans if s.name == name)

    def aggregate(self) -> dict[str, dict[str, float]]:
        """Per-name rollup: count / total ms / max ms / min ms / error count."""
        result: dict[str, dict[str, float]] = {}
        for span in self.spans:
            bucket = result.setdefault(
                span.name,
                {"count": 0, "ms": 0.0, "ms_max": 0.0, "ms_min": 0.0, "errors": 0},
            )
            bucket["count"] += 1
            bucket["ms"] += span.ms
            bucket["ms_max"] = max(bucket["ms_max"], span.ms)
            bucket["ms_min"] = span.ms if bucket["count"] == 1 else min(bucket["ms_min"], span.ms)
            if span.status == STATUS_ERROR:
                bucket["errors"] += 1
        for bucket in result.values():
            bucket["ms"] = round(bucket["ms"], 3)
            bucket["ms_max"] = round(bucket["ms_max"], 3)
            bucket["ms_min"] = round(bucket["ms_min"], 3)
        return result

    def context_ms(self) -> float:
        return round(sum(self.total_ms(name) for name in CONTEXT_SPANS), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "wall_ms": round(self.wall_ms, 3),
            "context_ms": self.context_ms(),
            "spans": [s.to_dict() for s in self.spans],
            "aggregate": self.aggregate(),
        }


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100). Empty input -> 0.0.

    Nearest-rank is used on purpose: with n=20 samples it is stable and
    reproducible, unlike interpolated percentiles which differ per library.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round((pct / 100.0) * len(ordered))))
    return float(ordered[min(rank, len(ordered)) - 1])
