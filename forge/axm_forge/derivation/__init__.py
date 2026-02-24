"""
AXM Forge — Derivation Passes

Post-processing passes that run after stage2 (binder) and before Genesis compilation.
Each pass reads candidates.jsonl and emits additional ext/ parquet data.

Available passes:
  temporal   — detect date/time claims, emit ext/temporal.parquet
  confidence — aggregate per-chunk extraction confidence
"""
from .temporal import run_temporal_pass
from .confidence import run_confidence_pass

__all__ = ["run_temporal_pass", "run_confidence_pass"]
