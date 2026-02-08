"""
Pattern Bundles - Cryptographically Protected Knowledge Exchange

Pattern Bundles are compact, transferable representations of successful
local behavior. They capture emergent doctrine that can be shared across
nodes, tested locally, and adopted or rejected.

PATTERN BUNDLE STRUCTURE:
{
  "bundle_id": "<unique id>",
  "bundle_version": "1.0",
  
  "context": {
    "domain": "air_defense",
    "scenario_type": "saturating_swarm",
    "environment": {...},
    "constraints": {...}
  },
  
  "pattern": {
    "type": "graph_rewrite|threshold|classification|response",
    "name": "velocity_coherence_clustering",
    "description": "...",
    "rules": [...],
    "parameters": {...}
  },
  
  "performance": {
    "baseline_metric": 0.65,
    "improved_metric": 0.89,
    "improvement_pct": 37,
    "confidence_interval": [0.85, 0.93],
    "sample_size": 147,
    "test_duration_sec": 3600
  },
  
  "provenance": {
    "origin_node_id": "node_alpha_7",
    "origin_timestamp": "2026-01-08T00:00:00Z",
    "origin_engagement": "engagement_2026_001",
    "chain": [
      {"node": "node_alpha_7", "action": "created", "ts": "..."},
      {"node": "node_beta_3", "action": "tested", "ts": "..."},
      {"node": "node_gamma_1", "action": "adopted", "ts": "..."}
    ]
  },
  
  "signature": {
    "algorithm": "Ed25519",
    "signer_id": "node_alpha_7",
    "public_key_b64": "...",
    "signature_b64": "..."
  },
  
  "topology_binding": {
    "required_entities": ["threat", "interceptor", "defended_asset"],
    "required_predicates": ["targets", "defends", "engages"],
    "topology_hash_b64": "..."
  }
}

BUNDLE LIFECYCLE:
1. DISCOVER: Node operates under stress, discovers effective behavior
2. HARVEST: Behavior is abstracted into Pattern Bundle
3. SIGN: Bundle is signed by originating node
4. SHARE: Bundle is distributed to nearby/similar nodes
5. TEST: Receiving nodes test in shadow mode (FAST)
6. EVALUATE: Performance compared to baseline (BEST)
7. ADOPT/REJECT: If improvement > threshold, adopt (SPREAD)
8. DECAY: Unused patterns decay over time

GRAPHKDF INTEGRATION:
Pattern Bundles can be encrypted with GraphKDF where:
- topology_hash is computed from the bundle's rules graph
- Different clearance levels get different partitions
- Topology binding prevents rule tampering
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        PrivateFormat,
        NoEncryption,
    )
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from .core import (
    Edge,
    compute_topology_hash,
    GraphKDFParams,
    encrypt_partition,
    Partition,
    FileEntry,
)


# ============================================================================
# PATTERN TYPES
# ============================================================================

class PatternType:
    """Types of patterns that can be captured."""
    GRAPH_REWRITE = "graph_rewrite"      # Modify graph structure
    THRESHOLD = "threshold"               # Adjust decision thresholds
    CLASSIFICATION = "classification"     # Classify entities differently
    RESPONSE = "response"                 # Change response behavior
    FUSION = "fusion"                     # Fuse data differently
    PRIORITIZATION = "prioritization"     # Change priority ordering


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class ContextSpec:
    """Context in which pattern was discovered/applies."""
    domain: str                           # e.g., "air_defense", "medical", "legal"
    scenario_type: str                    # e.g., "saturating_swarm", "mass_casualty"
    environment: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    def matches(self, other: "ContextSpec", threshold: float = 0.7) -> float:
        """Compute context similarity score (0-1)."""
        score = 0.0
        total = 0.0
        
        # Domain match
        total += 1.0
        if self.domain == other.domain:
            score += 1.0
        
        # Scenario match
        total += 1.0
        if self.scenario_type == other.scenario_type:
            score += 1.0
        
        # Tag overlap
        if self.tags and other.tags:
            total += 1.0
            overlap = len(set(self.tags) & set(other.tags))
            union = len(set(self.tags) | set(other.tags))
            score += overlap / union if union > 0 else 0
        
        return score / total if total > 0 else 0.0


@dataclass
class PatternRule:
    """A single rule within a pattern."""
    rule_id: str
    condition: Dict[str, Any]             # When to apply
    action: Dict[str, Any]                # What to do
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "condition": self.condition,
            "action": self.action,
            "parameters": self.parameters,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PatternRule":
        return cls(
            rule_id=d["rule_id"],
            condition=d["condition"],
            action=d["action"],
            parameters=d.get("parameters", {}),
        )


@dataclass
class PatternSpec:
    """The pattern itself - abstract rules/behaviors."""
    pattern_type: str
    name: str
    description: str
    rules: List[PatternRule] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def to_edges(self) -> List[Edge]:
        """Extract edges from pattern rules for topology hashing."""
        edges = []
        for rule in self.rules:
            # Extract subject/predicate/object from condition and action
            cond = rule.condition
            if "subject" in cond and "predicate" in cond and "object" in cond:
                edges.append(Edge(
                    subject=str(cond["subject"]),
                    predicate=str(cond["predicate"]),
                    object=str(cond["object"]),
                ))
            
            action = rule.action
            if "subject" in action and "predicate" in action and "object" in action:
                edges.append(Edge(
                    subject=str(action["subject"]),
                    predicate=str(action["predicate"]),
                    object=str(action["object"]),
                ))
        
        return edges


@dataclass
class PerformanceMetrics:
    """Performance improvement from pattern."""
    baseline_metric: float
    improved_metric: float
    metric_name: str = "accuracy"
    confidence_interval: Tuple[float, float] = (0.0, 1.0)
    sample_size: int = 0
    test_duration_sec: int = 0
    
    @property
    def improvement_pct(self) -> float:
        if self.baseline_metric == 0:
            return 0.0
        return ((self.improved_metric - self.baseline_metric) / self.baseline_metric) * 100


@dataclass
class ProvenanceEntry:
    """Entry in provenance chain."""
    node_id: str
    action: str  # created, tested, adopted, modified, rejected
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Provenance:
    """Full provenance of pattern."""
    origin_node_id: str
    origin_timestamp: str
    origin_engagement: str = ""
    chain: List[ProvenanceEntry] = field(default_factory=list)
    
    def add_entry(self, node_id: str, action: str, details: Optional[Dict] = None):
        self.chain.append(ProvenanceEntry(
            node_id=node_id,
            action=action,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=details or {},
        ))


@dataclass
class TopologyBinding:
    """Topology requirements for pattern applicability."""
    required_entities: List[str] = field(default_factory=list)
    required_predicates: List[str] = field(default_factory=list)
    topology_hash_b64: str = ""


# ============================================================================
# PATTERN BUNDLE
# ============================================================================

@dataclass
class PatternBundle:
    """A complete pattern bundle ready for sharing."""
    bundle_id: str
    context: ContextSpec
    pattern: PatternSpec
    performance: PerformanceMetrics
    provenance: Provenance
    topology_binding: TopologyBinding
    
    # Signature (optional, added after creation)
    signature_algorithm: str = ""
    signer_id: str = ""
    public_key_b64: str = ""
    signature_b64: str = ""
    
    # Metadata
    created_at: str = ""
    expires_at: str = ""
    version: str = "1.0"
    
    def compute_topology_hash(self) -> bytes:
        """Compute topology hash from pattern rules."""
        edges = self.pattern.to_edges()
        return compute_topology_hash(edges)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.version,
            "context": {
                "domain": self.context.domain,
                "scenario_type": self.context.scenario_type,
                "environment": self.context.environment,
                "constraints": self.context.constraints,
                "tags": self.context.tags,
            },
            "pattern": {
                "type": self.pattern.pattern_type,
                "name": self.pattern.name,
                "description": self.pattern.description,
                "rules": [r.to_dict() for r in self.pattern.rules],
                "parameters": self.pattern.parameters,
            },
            "performance": {
                "metric_name": self.performance.metric_name,
                "baseline_metric": self.performance.baseline_metric,
                "improved_metric": self.performance.improved_metric,
                "improvement_pct": self.performance.improvement_pct,
                "confidence_interval": list(self.performance.confidence_interval),
                "sample_size": self.performance.sample_size,
                "test_duration_sec": self.performance.test_duration_sec,
            },
            "provenance": {
                "origin_node_id": self.provenance.origin_node_id,
                "origin_timestamp": self.provenance.origin_timestamp,
                "origin_engagement": self.provenance.origin_engagement,
                "chain": [
                    {
                        "node": e.node_id,
                        "action": e.action,
                        "ts": e.timestamp,
                        "details": e.details,
                    }
                    for e in self.provenance.chain
                ],
            },
            "topology_binding": {
                "required_entities": self.topology_binding.required_entities,
                "required_predicates": self.topology_binding.required_predicates,
                "topology_hash_b64": self.topology_binding.topology_hash_b64,
            },
            "signature": {
                "algorithm": self.signature_algorithm,
                "signer_id": self.signer_id,
                "public_key_b64": self.public_key_b64,
                "signature_b64": self.signature_b64,
            } if self.signature_b64 else None,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PatternBundle":
        ctx = d.get("context", {})
        pat = d.get("pattern", {})
        perf = d.get("performance", {})
        prov = d.get("provenance", {})
        topo = d.get("topology_binding", {})
        sig = d.get("signature") or {}
        
        return cls(
            bundle_id=d["bundle_id"],
            context=ContextSpec(
                domain=ctx.get("domain", ""),
                scenario_type=ctx.get("scenario_type", ""),
                environment=ctx.get("environment", {}),
                constraints=ctx.get("constraints", {}),
                tags=ctx.get("tags", []),
            ),
            pattern=PatternSpec(
                pattern_type=pat.get("type", ""),
                name=pat.get("name", ""),
                description=pat.get("description", ""),
                rules=[PatternRule.from_dict(r) for r in pat.get("rules", [])],
                parameters=pat.get("parameters", {}),
            ),
            performance=PerformanceMetrics(
                metric_name=perf.get("metric_name", ""),
                baseline_metric=perf.get("baseline_metric", 0),
                improved_metric=perf.get("improved_metric", 0),
                confidence_interval=tuple(perf.get("confidence_interval", [0, 1])),
                sample_size=perf.get("sample_size", 0),
                test_duration_sec=perf.get("test_duration_sec", 0),
            ),
            provenance=Provenance(
                origin_node_id=prov.get("origin_node_id", ""),
                origin_timestamp=prov.get("origin_timestamp", ""),
                origin_engagement=prov.get("origin_engagement", ""),
                chain=[
                    ProvenanceEntry(
                        node_id=e.get("node", ""),
                        action=e.get("action", ""),
                        timestamp=e.get("ts", ""),
                        details=e.get("details", {}),
                    )
                    for e in prov.get("chain", [])
                ],
            ),
            topology_binding=TopologyBinding(
                required_entities=topo.get("required_entities", []),
                required_predicates=topo.get("required_predicates", []),
                topology_hash_b64=topo.get("topology_hash_b64", ""),
            ),
            signature_algorithm=sig.get("algorithm", ""),
            signer_id=sig.get("signer_id", ""),
            public_key_b64=sig.get("public_key_b64", ""),
            signature_b64=sig.get("signature_b64", ""),
            created_at=d.get("created_at", ""),
            expires_at=d.get("expires_at", ""),
            version=d.get("bundle_version", "1.0"),
        )
    
    def canonical_bytes(self) -> bytes:
        """Get canonical bytes for signing (excludes signature field)."""
        d = self.to_dict()
        d.pop("signature", None)
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")
    
    def sign(self, private_key_bytes: bytes, signer_id: str) -> None:
        """Sign the bundle with Ed25519."""
        if not HAS_CRYPTO:
            raise RuntimeError("cryptography library required for signing")
        
        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        public_key = private_key.public_key()
        
        signature = private_key.sign(self.canonical_bytes())
        
        self.signature_algorithm = "Ed25519"
        self.signer_id = signer_id
        self.public_key_b64 = base64.b64encode(
            public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode("ascii")
        self.signature_b64 = base64.b64encode(signature).decode("ascii")
    
    def verify_signature(self) -> bool:
        """Verify the bundle signature."""
        if not self.signature_b64:
            return False
        
        if not HAS_CRYPTO:
            raise RuntimeError("cryptography library required for verification")
        
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(self.public_key_b64)
            )
            signature = base64.b64decode(self.signature_b64)
            public_key.verify(signature, self.canonical_bytes())
            return True
        except Exception:
            return False


# ============================================================================
# BUNDLE CREATION
# ============================================================================

def create_bundle(
    node_id: str,
    domain: str,
    scenario_type: str,
    pattern_name: str,
    pattern_type: str,
    rules: List[Dict[str, Any]],
    baseline_metric: float,
    improved_metric: float,
    metric_name: str = "accuracy",
    description: str = "",
    engagement_id: str = "",
    tags: Optional[List[str]] = None,
    environment: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    sample_size: int = 0,
    test_duration_sec: int = 0,
) -> PatternBundle:
    """Create a new Pattern Bundle from discovered behavior.
    
    Args:
        node_id: ID of the node creating the bundle
        domain: Domain (e.g., "air_defense", "medical")
        scenario_type: Type of scenario (e.g., "saturating_swarm")
        pattern_name: Human-readable name
        pattern_type: Type of pattern (see PatternType)
        rules: List of rule dicts with condition/action
        baseline_metric: Performance before pattern
        improved_metric: Performance with pattern
        metric_name: Name of metric being measured
        description: Human-readable description
        engagement_id: ID of engagement where discovered
        tags: Context tags
        environment: Environment conditions
        constraints: Operating constraints
        parameters: Pattern parameters
        sample_size: Number of samples tested
        test_duration_sec: Duration of testing
    
    Returns:
        Unsigned PatternBundle ready for signing
    """
    bundle_id = f"pb_{secrets.token_hex(8)}"
    now = datetime.now(timezone.utc).isoformat()
    
    # Convert rules
    pattern_rules = [
        PatternRule(
            rule_id=f"rule_{i}",
            condition=r.get("condition", {}),
            action=r.get("action", {}),
            parameters=r.get("parameters", {}),
        )
        for i, r in enumerate(rules)
    ]
    
    # Create pattern
    pattern = PatternSpec(
        pattern_type=pattern_type,
        name=pattern_name,
        description=description,
        rules=pattern_rules,
        parameters=parameters or {},
    )
    
    # Create context
    context = ContextSpec(
        domain=domain,
        scenario_type=scenario_type,
        environment=environment or {},
        constraints=constraints or {},
        tags=tags or [],
    )
    
    # Create performance
    performance = PerformanceMetrics(
        metric_name=metric_name,
        baseline_metric=baseline_metric,
        improved_metric=improved_metric,
        sample_size=sample_size,
        test_duration_sec=test_duration_sec,
    )
    
    # Create provenance
    provenance = Provenance(
        origin_node_id=node_id,
        origin_timestamp=now,
        origin_engagement=engagement_id,
        chain=[ProvenanceEntry(
            node_id=node_id,
            action="created",
            timestamp=now,
        )],
    )
    
    # Create bundle
    bundle = PatternBundle(
        bundle_id=bundle_id,
        context=context,
        pattern=pattern,
        performance=performance,
        provenance=provenance,
        topology_binding=TopologyBinding(),
        created_at=now,
    )
    
    # Compute topology hash from rules
    topo_hash = bundle.compute_topology_hash()
    bundle.topology_binding.topology_hash_b64 = base64.b64encode(topo_hash).decode("ascii")
    
    # Extract required entities/predicates from rules
    entities = set()
    predicates = set()
    for rule in pattern_rules:
        for key in ["condition", "action"]:
            d = getattr(rule, key, {})
            if "subject" in d:
                entities.add(str(d["subject"]))
            if "object" in d:
                entities.add(str(d["object"]))
            if "predicate" in d:
                predicates.add(str(d["predicate"]))
    
    bundle.topology_binding.required_entities = sorted(entities)
    bundle.topology_binding.required_predicates = sorted(predicates)
    
    return bundle


# ============================================================================
# BUNDLE EVALUATION (FAST/BEST/SPREAD)
# ============================================================================

@dataclass
class EvaluationResult:
    """Result of evaluating a pattern bundle."""
    bundle_id: str
    context_match_score: float          # How well context matches local
    performance_improvement: float       # Measured improvement
    stability_score: float              # How stable the improvement is
    recommendation: str                 # "adopt", "reject", "test_more"
    details: Dict[str, Any] = field(default_factory=dict)


class BundleEvaluator:
    """Evaluates Pattern Bundles for local adoption.
    
    Implements FAST/BEST logic:
    - FAST: Test in shadow mode
    - BEST: Evaluate performance improvement
    """
    
    def __init__(
        self,
        node_id: str,
        local_context: ContextSpec,
        adoption_threshold: float = 0.1,      # 10% improvement required
        context_match_threshold: float = 0.6,  # 60% context match required
    ):
        self.node_id = node_id
        self.local_context = local_context
        self.adoption_threshold = adoption_threshold
        self.context_match_threshold = context_match_threshold
        self._shadow_results: Dict[str, List[float]] = {}
    
    def check_context_fit(self, bundle: PatternBundle) -> float:
        """Check how well bundle's context matches local context."""
        return self.local_context.matches(bundle.context)
    
    def start_shadow_test(self, bundle: PatternBundle) -> bool:
        """Start shadow testing of a bundle (FAST)."""
        context_score = self.check_context_fit(bundle)
        if context_score < self.context_match_threshold:
            return False
        
        self._shadow_results[bundle.bundle_id] = []
        return True
    
    def record_shadow_result(self, bundle_id: str, metric_value: float) -> None:
        """Record a shadow test result."""
        if bundle_id in self._shadow_results:
            self._shadow_results[bundle_id].append(metric_value)
    
    def evaluate(self, bundle: PatternBundle) -> EvaluationResult:
        """Evaluate a bundle for adoption (BEST)."""
        context_score = self.check_context_fit(bundle)
        
        # Check shadow results
        shadow = self._shadow_results.get(bundle.bundle_id, [])
        if shadow:
            avg_improvement = sum(shadow) / len(shadow)
            variance = sum((x - avg_improvement) ** 2 for x in shadow) / len(shadow)
            stability = 1.0 / (1.0 + variance)
        else:
            # Use bundle's reported metrics
            avg_improvement = bundle.performance.improvement_pct / 100
            stability = 0.5  # Lower confidence without local testing
        
        # Determine recommendation
        if context_score < self.context_match_threshold:
            recommendation = "reject"
            reason = "Context mismatch"
        elif avg_improvement < self.adoption_threshold:
            recommendation = "reject"
            reason = "Insufficient improvement"
        elif len(shadow) < 10:
            recommendation = "test_more"
            reason = "More shadow testing needed"
        else:
            recommendation = "adopt"
            reason = "Meets adoption criteria"
        
        return EvaluationResult(
            bundle_id=bundle.bundle_id,
            context_match_score=context_score,
            performance_improvement=avg_improvement,
            stability_score=stability,
            recommendation=recommendation,
            details={
                "reason": reason,
                "shadow_samples": len(shadow),
                "local_tests": shadow[-5:] if shadow else [],
            },
        )


# ============================================================================
# BUNDLE REGISTRY
# ============================================================================

class BundleRegistry:
    """Local registry of Pattern Bundles.
    
    Tracks:
    - Received bundles
    - Adopted bundles
    - Created bundles
    - Evaluation history
    """
    
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path
        self._bundles: Dict[str, PatternBundle] = {}
        self._status: Dict[str, str] = {}  # bundle_id -> status
        self._evaluations: Dict[str, List[EvaluationResult]] = {}
        
        if storage_path and storage_path.exists():
            self._load()
    
    def add(self, bundle: PatternBundle, status: str = "received") -> None:
        """Add a bundle to the registry."""
        self._bundles[bundle.bundle_id] = bundle
        self._status[bundle.bundle_id] = status
        self._save()
    
    def get(self, bundle_id: str) -> Optional[PatternBundle]:
        """Get a bundle by ID."""
        return self._bundles.get(bundle_id)
    
    def set_status(self, bundle_id: str, status: str) -> None:
        """Update bundle status."""
        if bundle_id in self._bundles:
            self._status[bundle_id] = status
            self._save()
    
    def record_evaluation(self, result: EvaluationResult) -> None:
        """Record an evaluation result."""
        if result.bundle_id not in self._evaluations:
            self._evaluations[result.bundle_id] = []
        self._evaluations[result.bundle_id].append(result)
        self._save()
    
    def list_by_status(self, status: str) -> List[PatternBundle]:
        """List bundles by status."""
        return [
            b for bid, b in self._bundles.items()
            if self._status.get(bid) == status
        ]
    
    def find_matching(self, context: ContextSpec, threshold: float = 0.6) -> List[PatternBundle]:
        """Find bundles matching a context."""
        matches = []
        for bundle in self._bundles.values():
            if context.matches(bundle.context) >= threshold:
                matches.append(bundle)
        return matches
    
    def _save(self) -> None:
        if self.storage_path:
            data = {
                "bundles": {bid: b.to_dict() for bid, b in self._bundles.items()},
                "status": self._status,
            }
            self.storage_path.write_text(json.dumps(data, indent=2))
    
    def _load(self) -> None:
        if self.storage_path and self.storage_path.exists():
            data = json.loads(self.storage_path.read_text())
            self._bundles = {
                bid: PatternBundle.from_dict(b)
                for bid, b in data.get("bundles", {}).items()
            }
            self._status = data.get("status", {})
