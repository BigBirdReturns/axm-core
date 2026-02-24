"""
Confidence Derivation Pass

Reads candidates.jsonl, computes per-predicate extraction confidence statistics,
writes a summary report (JSON, not parquet — confidence is metadata, not a shard
extension needed for queries).

Can optionally be extended to write ext/confidence.parquet when a formal
schema is ratified in EXTENSIONS_REGISTRY.md.

Adapted from axm-kg derive/confidence.py — simplified to operate on
candidates.jsonl tier+confidence fields rather than the old Program model.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def run_confidence_pass(
    candidates_path: Path,
    out_dir: Path,
) -> Dict[str, Any]:
    """
    Aggregate extraction confidence from candidates.jsonl.

    Writes: {out_dir}/confidence_summary.json
    Returns stats dict.
    """
    tier_buckets: Dict[int, List[float]] = defaultdict(list)
    pred_counts: Dict[str, int] = defaultdict(int)
    total = 0

    with candidates_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            tier = int(c.get("tier", 3))
            conf = float(c.get("confidence", 0.7))
            pred = c.get("predicate", "unknown")
            tier_buckets[tier].append(conf)
            pred_counts[pred] += 1
            total += 1

    if total == 0:
        return {"total": 0, "written": False}

    tier_summary = {}
    for tier, confs in sorted(tier_buckets.items()):
        tier_summary[f"tier{tier}"] = {
            "count": len(confs),
            "mean": round(sum(confs) / len(confs), 4),
            "min": round(min(confs), 4),
            "max": round(max(confs), 4),
        }

    overall_confs = [c for confs in tier_buckets.values() for c in confs]
    overall_mean = sum(overall_confs) / len(overall_confs) if overall_confs else 0.0

    top_predicates = sorted(pred_counts.items(), key=lambda x: x[1], reverse=True)[:20]

    summary = {
        "total_candidates": total,
        "overall_mean_confidence": round(overall_mean, 4),
        "by_tier": tier_summary,
        "top_predicates": [{"predicate": p, "count": n} for p, n in top_predicates],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "confidence_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {"total": total, "mean": overall_mean, "written": True, "path": str(out_path)}
