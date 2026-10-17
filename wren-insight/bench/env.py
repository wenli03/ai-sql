"""Machine / environment snapshot collected alongside every benchmark run.

A timing number is meaningless without the environment it was taken on, and the
"resource footprint" targets (install size, first-screen bytes, resident memory)
are environment facts rather than per-question timings — so they live here.

Stdlib only. No new dependency is introduced by the benchmark.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import sysconfig
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"

#: Files the browser must fetch before the first screen is interactive.
#: ECharts is lazy-loaded on the first chart, so it is deliberately not part
#: of this tuple; the benchmark's first-screen budget tracks the initial app
#: shell, not the deferred chart renderer.
FIRST_SCREEN_ASSETS = (
    "index.html",
    "styles.css",
    "app.js",
)


def python_info() -> dict[str, Any]:
    return {
        "version": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def dependency_stats() -> dict[str, Any]:
    """Installed distributions and the on-disk size of site-packages."""
    names = sorted({d.metadata["Name"] or "" for d in distributions() if d.metadata["Name"]})
    purelib = sysconfig.get_paths().get("purelib") or ""
    total = 0
    if purelib and os.path.isdir(purelib):
        for root, _dirs, files in os.walk(purelib):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    return {
        "distribution_count": len(names),
        "site_packages_bytes": total,
        "site_packages_mb": round(total / 1024 / 1024, 1),
        "site_packages_path": purelib,
        "top_level": names,
    }


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def web_assets() -> dict[str, Any]:
    files: dict[str, int] = {}
    if WEB_ROOT.is_dir():
        for path in WEB_ROOT.rglob("*"):
            if path.is_file():
                size = _size(path)
                if size is not None:
                    files[str(path.relative_to(WEB_ROOT)).replace("\\", "/")] = size
    first_screen = sum(files.get(name, 0) for name in FIRST_SCREEN_ASSETS)
    return {
        "web_root": str(WEB_ROOT),
        "files": files,
        "total_bytes": sum(files.values()),
        "first_screen_bytes": first_screen,
        "first_screen_kb": round(first_screen / 1024, 1),
        "missing_first_screen_assets": [
            name for name in FIRST_SCREEN_ASSETS if name not in files
        ],
    }


def rss_mb() -> float | None:
    """Resident set size of this process in MB.

    Windows-only via ctypes/psapi; returns None elsewhere rather than guessing.
    """
    if os.name != "nt":
        return None
    try:

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
            ctypes.windll.kernel32.GetCurrentProcess(),  # type: ignore[attr-defined]
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            return None
        return round(counters.WorkingSetSize / 1024 / 1024, 1)
    except Exception:  # noqa: BLE001 - environment probe must never break a run
        return None


def llm_info() -> dict[str, Any]:
    from app import config

    return {
        "base_url": config.LLM_BASE_URL,
        "model": config.LLM_MODEL,
        "timeout_seconds": config.LLM_TIMEOUT_SECONDS,
        "max_rows": config.MAX_ROWS,
        "max_repair_attempts": config.MAX_REPAIR_ATTEMPTS,
    }


def snapshot() -> dict[str, Any]:
    return {
        "python": python_info(),
        "dependencies": dependency_stats(),
        "web": web_assets(),
        "rss_mb": rss_mb(),
        "llm": llm_info(),
    }
