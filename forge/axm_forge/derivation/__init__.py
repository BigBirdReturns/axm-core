"""
AXM Forge — Derivation Passes

Post-processing passes that run after stage2 (binder) and before Genesis
compilation.

Genesis v1 path: `annotate_temporal_candidates` adds temporal keys to
candidates.jsonl in place; the v1 compiler emits the sealed
ext/temporal@1.jsonl itself. Parquet-writing passes (run_temporal_pass,
run_coords_pass, run_confidence_pass) produce LOCAL DERIVED CACHES that
live outside any shard — v1 shards carry no Parquet.
"""
from .temporal import annotate_temporal_candidates, run_temporal_pass
from .confidence import run_confidence_pass

__all__ = ["annotate_temporal_candidates", "run_temporal_pass", "run_confidence_pass"]
