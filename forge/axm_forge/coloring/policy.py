from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from pathlib import Path
import yaml

@dataclass(frozen=True)
class ColoringRule:
    trigger: str
    match: object
    color: str

@dataclass(frozen=True)
class ColoringPolicy:
    default_color: str
    rules: List[ColoringRule]

def load_policy(path: Optional[Path]) -> ColoringPolicy:
    if path is None:
        return ColoringPolicy(default_color="Green", rules=[])
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_color = str(data.get("default_color", "Green"))
    rules = []
    for r in data.get("rules", []) or []:
        rules.append(ColoringRule(
            trigger=str(r.get("trigger", "")),
            match=r.get("match"),
            color=str(r.get("color", "Green")),
        ))
    return ColoringPolicy(default_color=default_color, rules=rules)

def classify_text(policy: ColoringPolicy, text: str, metadata: Dict) -> str:
    color = policy.default_color
    t = text.lower()
    for r in policy.rules:
        if r.trigger == "content_keyword":
            needles = r.match if isinstance(r.match, list) else [r.match]
            for n in needles:
                if not n:
                    continue
                if str(n).lower() in t:
                    return r.color
        if r.trigger == "source_metadata":
            m = str(r.match).lower()
            tags = [str(x).lower() for x in (metadata.get("tags") or [])]
            if m in tags:
                return r.color
    return color
