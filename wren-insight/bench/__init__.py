"""Baseline benchmark for the ask pipeline.

Why this exists: every "we are faster / smaller / easier" claim in the restart
plan has to be measured against a number taken from *this* machine, not from a
README. `runner.py` produces that number; `thresholds.json` turns it into a
pass/fail gate once the target is agreed.
"""
