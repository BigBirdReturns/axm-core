"""
Adaptive Layer - FAST/BEST/SPREAD Implementation

This module implements the adaptive mechanisms for emergent doctrine:

FAST (Local Hypothesis Testing):
- Shadow subgraphs for testing alternative interpretations
- No impact on production state
- Quick iteration on hypotheses

BEST (Performance Evaluation):
- Short-window evaluation of rule performance
- Promotes rules that improve stability/accuracy
- Rejects rules that degrade performance

SPREAD (Controlled Propagation):
- Share validated patterns to similar nodes
- Context-based filtering
- Prevents contamination from incompatible contexts
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import threading

from .bundles import (
    PatternBundle,
    PatternRule,
    ContextSpec,
    EvaluationResult,
    BundleEvaluator,
    BundleRegistry,
    create_bundle,
    PatternType,
)


# ============================================================================
# SHADOW SUBGRAPH (FAST)
# ============================================================================

class ShadowMode(Enum):
    """Shadow testing modes."""
    PARALLEL = "parallel"      # Run alongside production
    SEQUENTIAL = "sequential"  # Run before production (for comparison)
    ISOLATED = "isolated"      # Run completely separate


@dataclass
class ShadowResult:
    """Result from shadow subgraph execution."""
    hypothesis_id: str
    input_hash: str
    shadow_output: Any
    production_output: Any
    shadow_latency_ms: float
    production_latency_ms: float
    metrics: Dict[str, float] = field(default_factory=dict)
    timestamp: str = ""


class ShadowSubgraph:
    """A shadow subgraph for testing hypotheses (FAST).
    
    Shadow subgraphs allow testing alternative interpretations
    without affecting production state. They run in parallel
    with the main graph and collect performance metrics.
    """
    
    def __init__(
        self,
        hypothesis_id: str,
        rules: List[PatternRule],
        mode: ShadowMode = ShadowMode.PARALLEL,
        max_history: int = 100,
    ):
        self.hypothesis_id = hypothesis_id
        self.rules = rules
        self.mode = mode
        self.max_history = max_history
        
        self._results: deque[ShadowResult] = deque(maxlen=max_history)
        self._active = True
        self._created_at = datetime.now(timezone.utc).isoformat()
        self._metrics: Dict[str, List[float]] = {}
    
    def execute(
        self,
        input_data: Any,
        production_executor: Callable[[Any], Any],
        shadow_executor: Callable[[Any, List[PatternRule]], Any],
    ) -> ShadowResult:
        """Execute shadow and production in parallel, compare results."""
        input_hash = hashlib.sha256(
            json.dumps(input_data, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        
        # Production execution
        prod_start = time.perf_counter()
        prod_output = production_executor(input_data)
        prod_latency = (time.perf_counter() - prod_start) * 1000
        
        # Shadow execution
        shadow_start = time.perf_counter()
        shadow_output = shadow_executor(input_data, self.rules)
        shadow_latency = (time.perf_counter() - shadow_start) * 1000
        
        result = ShadowResult(
            hypothesis_id=self.hypothesis_id,
            input_hash=input_hash,
            shadow_output=shadow_output,
            production_output=prod_output,
            shadow_latency_ms=shadow_latency,
            production_latency_ms=prod_latency,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        
        self._results.append(result)
        return result
    
    def record_metric(self, name: str, value: float) -> None:
        """Record a performance metric."""
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append(value)
    
    def get_metrics_summary(self) -> Dict[str, Dict[str, float]]:
        """Get summary statistics for all metrics."""
        summary = {}
        for name, values in self._metrics.items():
            if values:
                summary[name] = {
                    "count": len(values),
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                }
        return summary
    
    def deactivate(self) -> None:
        """Deactivate shadow subgraph."""
        self._active = False
    
    @property
    def is_active(self) -> bool:
        return self._active
    
    @property
    def sample_count(self) -> int:
        return len(self._results)


# ============================================================================
# PERFORMANCE EVALUATOR (BEST)
# ============================================================================

@dataclass
class PerformanceWindow:
    """A time window for performance evaluation."""
    start_time: float
    end_time: float
    metrics: Dict[str, List[float]] = field(default_factory=dict)
    
    @property
    def duration_sec(self) -> float:
        return self.end_time - self.start_time


class PerformanceEvaluator:
    """Evaluates rule performance over time windows (BEST).
    
    Tracks performance metrics and determines whether rules
    should be promoted, demoted, or maintained.
    """
    
    def __init__(
        self,
        window_duration_sec: float = 300,  # 5 minute windows
        promotion_threshold: float = 0.1,   # 10% improvement to promote
        demotion_threshold: float = -0.05,  # 5% degradation to demote
    ):
        self.window_duration_sec = window_duration_sec
        self.promotion_threshold = promotion_threshold
        self.demotion_threshold = demotion_threshold
        
        self._windows: Dict[str, List[PerformanceWindow]] = {}  # rule_id -> windows
        self._current_window: Dict[str, PerformanceWindow] = {}
        self._baseline: Dict[str, float] = {}  # metric_name -> baseline value
    
    def set_baseline(self, metric_name: str, value: float) -> None:
        """Set baseline value for a metric."""
        self._baseline[metric_name] = value
    
    def record(self, rule_id: str, metric_name: str, value: float) -> None:
        """Record a metric value for a rule."""
        now = time.time()
        
        # Check if we need a new window
        if rule_id not in self._current_window:
            self._current_window[rule_id] = PerformanceWindow(
                start_time=now,
                end_time=now + self.window_duration_sec,
            )
            self._windows.setdefault(rule_id, [])
        
        window = self._current_window[rule_id]
        
        # Rotate window if needed
        if now > window.end_time:
            self._windows[rule_id].append(window)
            self._current_window[rule_id] = PerformanceWindow(
                start_time=now,
                end_time=now + self.window_duration_sec,
            )
            window = self._current_window[rule_id]
        
        # Record metric
        if metric_name not in window.metrics:
            window.metrics[metric_name] = []
        window.metrics[metric_name].append(value)
    
    def evaluate(self, rule_id: str, metric_name: str) -> Tuple[str, float]:
        """Evaluate a rule's performance.
        
        Returns:
            (recommendation, improvement_pct)
            recommendation is one of: "promote", "demote", "maintain"
        """
        if rule_id not in self._windows or not self._windows[rule_id]:
            return ("maintain", 0.0)
        
        baseline = self._baseline.get(metric_name, 0)
        if baseline == 0:
            return ("maintain", 0.0)
        
        # Get recent windows
        recent_windows = self._windows[rule_id][-5:]  # Last 5 windows
        
        all_values = []
        for w in recent_windows:
            all_values.extend(w.metrics.get(metric_name, []))
        
        if not all_values:
            return ("maintain", 0.0)
        
        avg_value = sum(all_values) / len(all_values)
        improvement = (avg_value - baseline) / baseline
        
        if improvement >= self.promotion_threshold:
            return ("promote", improvement)
        elif improvement <= self.demotion_threshold:
            return ("demote", improvement)
        else:
            return ("maintain", improvement)


# ============================================================================
# PATTERN PROPAGATOR (SPREAD)
# ============================================================================

@dataclass
class PropagationTarget:
    """A target node for pattern propagation."""
    node_id: str
    context: ContextSpec
    priority: int = 0
    last_contact: str = ""


class PatternPropagator:
    """Propagates validated patterns to similar nodes (SPREAD).
    
    Implements controlled propagation:
    - Context-based filtering
    - Priority ordering
    - Rate limiting
    - Contamination prevention
    """
    
    def __init__(
        self,
        local_node_id: str,
        local_context: ContextSpec,
        max_propagation_rate: int = 10,  # bundles per minute
        context_match_threshold: float = 0.6,
    ):
        self.local_node_id = local_node_id
        self.local_context = local_context
        self.max_propagation_rate = max_propagation_rate
        self.context_match_threshold = context_match_threshold
        
        self._targets: Dict[str, PropagationTarget] = {}
        self._propagation_history: List[Tuple[str, str, str]] = []  # (bundle_id, target_id, timestamp)
        self._last_propagation: Dict[str, float] = {}  # bundle_id -> timestamp
        self._lock = threading.Lock()
    
    def register_target(self, target: PropagationTarget) -> None:
        """Register a propagation target."""
        with self._lock:
            self._targets[target.node_id] = target
    
    def unregister_target(self, node_id: str) -> None:
        """Unregister a propagation target."""
        with self._lock:
            self._targets.pop(node_id, None)
    
    def find_compatible_targets(self, bundle: PatternBundle) -> List[PropagationTarget]:
        """Find targets compatible with a bundle's context."""
        compatible = []
        
        with self._lock:
            for target in self._targets.values():
                score = bundle.context.matches(target.context)
                if score >= self.context_match_threshold:
                    compatible.append(target)
        
        # Sort by context match score (best matches first)
        compatible.sort(
            key=lambda t: bundle.context.matches(t.context),
            reverse=True,
        )
        
        return compatible
    
    def should_propagate(self, bundle_id: str) -> bool:
        """Check if propagation is allowed (rate limiting)."""
        now = time.time()
        
        with self._lock:
            last = self._last_propagation.get(bundle_id, 0)
            if now - last < 60 / self.max_propagation_rate:
                return False
            
            self._last_propagation[bundle_id] = now
            return True
    
    def propagate(
        self,
        bundle: PatternBundle,
        send_func: Callable[[PatternBundle, str], bool],
    ) -> List[str]:
        """Propagate a bundle to compatible targets.
        
        Args:
            bundle: Pattern bundle to propagate
            send_func: Function to send bundle to a node (bundle, node_id) -> success
        
        Returns:
            List of node IDs that received the bundle
        """
        if not self.should_propagate(bundle.bundle_id):
            return []
        
        targets = self.find_compatible_targets(bundle)
        sent_to = []
        
        for target in targets:
            if target.node_id == self.local_node_id:
                continue
            
            try:
                if send_func(bundle, target.node_id):
                    sent_to.append(target.node_id)
                    
                    with self._lock:
                        self._propagation_history.append((
                            bundle.bundle_id,
                            target.node_id,
                            datetime.now(timezone.utc).isoformat(),
                        ))
            except Exception:
                pass  # Log error in production
        
        return sent_to


# ============================================================================
# ADAPTIVE CONTROLLER
# ============================================================================

@dataclass
class AdaptiveConfig:
    """Configuration for adaptive behavior."""
    # FAST config
    max_shadow_subgraphs: int = 5
    shadow_sample_threshold: int = 50  # Samples before evaluation
    
    # BEST config
    evaluation_window_sec: float = 300
    promotion_threshold: float = 0.1
    demotion_threshold: float = -0.05
    
    # SPREAD config
    propagation_rate: int = 10
    context_match_threshold: float = 0.6
    
    # General
    auto_adopt: bool = False  # Require human approval?
    decay_rate: float = 0.01  # Per-hour decay for unused patterns


class AdaptiveController:
    """Main controller for FAST/BEST/SPREAD adaptive behavior.
    
    Coordinates shadow testing, performance evaluation, and
    pattern propagation across the system.
    """
    
    def __init__(
        self,
        node_id: str,
        context: ContextSpec,
        config: AdaptiveConfig,
        registry: Optional[BundleRegistry] = None,
    ):
        self.node_id = node_id
        self.context = context
        self.config = config
        
        self.registry = registry or BundleRegistry()
        self.evaluator = BundleEvaluator(
            node_id=node_id,
            local_context=context,
            adoption_threshold=config.promotion_threshold,
            context_match_threshold=config.context_match_threshold,
        )
        self.performance = PerformanceEvaluator(
            window_duration_sec=config.evaluation_window_sec,
            promotion_threshold=config.promotion_threshold,
            demotion_threshold=config.demotion_threshold,
        )
        self.propagator = PatternPropagator(
            local_node_id=node_id,
            local_context=context,
            max_propagation_rate=config.propagation_rate,
            context_match_threshold=config.context_match_threshold,
        )
        
        self._shadows: Dict[str, ShadowSubgraph] = {}
        self._adopted_rules: Dict[str, PatternRule] = {}
        self._pending_human_review: List[str] = []
        self._lock = threading.Lock()
    
    # ---- FAST: Shadow Testing ----
    
    def start_shadow_test(self, bundle: PatternBundle) -> Optional[str]:
        """Start shadow testing a pattern bundle.
        
        Returns shadow ID if started, None if rejected.
        """
        with self._lock:
            if len(self._shadows) >= self.config.max_shadow_subgraphs:
                return None
            
            if not self.evaluator.start_shadow_test(bundle):
                return None
            
            shadow = ShadowSubgraph(
                hypothesis_id=bundle.bundle_id,
                rules=bundle.pattern.rules,
            )
            
            self._shadows[bundle.bundle_id] = shadow
            self.registry.add(bundle, status="testing")
            
            return bundle.bundle_id
    
    def record_shadow_result(
        self,
        bundle_id: str,
        input_data: Any,
        production_executor: Callable,
        shadow_executor: Callable,
    ) -> Optional[ShadowResult]:
        """Record a shadow test result."""
        shadow = self._shadows.get(bundle_id)
        if not shadow or not shadow.is_active:
            return None
        
        return shadow.execute(input_data, production_executor, shadow_executor)
    
    # ---- BEST: Performance Evaluation ----
    
    def evaluate_shadow(self, bundle_id: str) -> Optional[EvaluationResult]:
        """Evaluate a shadow-tested bundle."""
        shadow = self._shadows.get(bundle_id)
        bundle = self.registry.get(bundle_id)
        
        if not shadow or not bundle:
            return None
        
        if shadow.sample_count < self.config.shadow_sample_threshold:
            return EvaluationResult(
                bundle_id=bundle_id,
                context_match_score=self.evaluator.check_context_fit(bundle),
                performance_improvement=0,
                stability_score=0,
                recommendation="test_more",
                details={"samples": shadow.sample_count, "required": self.config.shadow_sample_threshold},
            )
        
        result = self.evaluator.evaluate(bundle)
        self.registry.record_evaluation(result)
        
        if result.recommendation == "adopt":
            if self.config.auto_adopt:
                self._adopt_bundle(bundle_id)
            else:
                self._pending_human_review.append(bundle_id)
        elif result.recommendation == "reject":
            self._reject_bundle(bundle_id)
        
        return result
    
    def _adopt_bundle(self, bundle_id: str) -> None:
        """Adopt a bundle's rules into production."""
        bundle = self.registry.get(bundle_id)
        if not bundle:
            return
        
        with self._lock:
            for rule in bundle.pattern.rules:
                self._adopted_rules[rule.rule_id] = rule
            
            shadow = self._shadows.pop(bundle_id, None)
            if shadow:
                shadow.deactivate()
            
            self.registry.set_status(bundle_id, "adopted")
            bundle.provenance.add_entry(self.node_id, "adopted")
    
    def _reject_bundle(self, bundle_id: str) -> None:
        """Reject a bundle."""
        with self._lock:
            shadow = self._shadows.pop(bundle_id, None)
            if shadow:
                shadow.deactivate()
            
            self.registry.set_status(bundle_id, "rejected")
    
    def approve_pending(self, bundle_id: str) -> bool:
        """Human approval of a pending bundle."""
        if bundle_id not in self._pending_human_review:
            return False
        
        self._pending_human_review.remove(bundle_id)
        self._adopt_bundle(bundle_id)
        return True
    
    def reject_pending(self, bundle_id: str) -> bool:
        """Human rejection of a pending bundle."""
        if bundle_id not in self._pending_human_review:
            return False
        
        self._pending_human_review.remove(bundle_id)
        self._reject_bundle(bundle_id)
        return True
    
    # ---- SPREAD: Pattern Propagation ----
    
    def propagate_bundle(
        self,
        bundle_id: str,
        send_func: Callable[[PatternBundle, str], bool],
    ) -> List[str]:
        """Propagate a bundle to compatible nodes."""
        bundle = self.registry.get(bundle_id)
        if not bundle:
            return []
        
        if self.registry._status.get(bundle_id) != "adopted":
            return []  # Only propagate adopted bundles
        
        sent_to = self.propagator.propagate(bundle, send_func)
        
        for node_id in sent_to:
            bundle.provenance.add_entry(
                self.node_id,
                "propagated",
                {"target": node_id},
            )
        
        return sent_to
    
    def receive_bundle(self, bundle: PatternBundle) -> str:
        """Receive a bundle from another node.
        
        Returns: "accepted", "rejected", or "testing"
        """
        # Verify signature
        if bundle.signature_b64 and not bundle.verify_signature():
            return "rejected"
        
        # Check context
        if self.evaluator.check_context_fit(bundle) < self.config.context_match_threshold:
            return "rejected"
        
        # Add to registry and start testing
        self.registry.add(bundle, status="received")
        bundle.provenance.add_entry(self.node_id, "received")
        
        if self.start_shadow_test(bundle):
            return "testing"
        else:
            return "queued"
    
    # ---- Utility ----
    
    def get_adopted_rules(self) -> List[PatternRule]:
        """Get all adopted rules."""
        with self._lock:
            return list(self._adopted_rules.values())
    
    def get_pending_review(self) -> List[PatternBundle]:
        """Get bundles pending human review."""
        return [
            self.registry.get(bid)
            for bid in self._pending_human_review
            if self.registry.get(bid)
        ]
    
    def get_status(self) -> Dict[str, Any]:
        """Get controller status."""
        with self._lock:
            return {
                "node_id": self.node_id,
                "active_shadows": len(self._shadows),
                "adopted_rules": len(self._adopted_rules),
                "pending_review": len(self._pending_human_review),
                "registered_targets": len(self.propagator._targets),
                "config": {
                    "auto_adopt": self.config.auto_adopt,
                    "promotion_threshold": self.config.promotion_threshold,
                },
            }
