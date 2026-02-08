from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from axm_forge.models.claims import Claim, ClaimGenContext

GeneratorFn = Callable[[ClaimGenContext], List[Claim]]

_REGISTRY: Dict[str, GeneratorFn] = {}

def register_generator(name: str) -> Callable[[GeneratorFn], GeneratorFn]:
    def deco(fn: GeneratorFn) -> GeneratorFn:
        if name in _REGISTRY:
            raise ValueError(f"Generator already registered: {name}")
        _REGISTRY[name] = fn
        return fn
    return deco

def list_generators() -> List[str]:
    return sorted(_REGISTRY.keys())

def run_generators(ctx: ClaimGenContext, enabled: List[str]) -> List[Claim]:
    claims: List[Claim] = []
    for name in enabled:
        fn = _REGISTRY.get(name)
        if fn is None:
            raise KeyError(f"Unknown generator: {name}. Available: {list_generators()}")
        claims.extend(fn(ctx))
    return claims
