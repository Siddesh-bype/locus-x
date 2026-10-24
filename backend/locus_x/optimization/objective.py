"""The documented placement objective.

    J = w_e · (RMSE / rmse_ref)
      + w_t · (P95  / p95_ref)
      + w_u · (1 − valid_rate)
      + w_g · poor_geometry_fraction
      + w_r · resilience_penalty

Definitions (protocol 1.0.0):

* **error_term** — pooled RMSE over the development split, normalized by a
  fixed reference scale so terms are comparable. The conventional (baseline)
  estimator is used during search; the estimator is never part of the layout
  decision surface beyond this fixed choice.
* **tail_term** — pooled P95 error, same normalization style.
* **unavailable_term** — fraction of frames with no acceptable fix.
* **geometry_term** — fraction of route sample points where the layout offers
  fewer than three LOS anchors or ill-conditioned range geometry
  (condition > 1000, the estimator's own rejection threshold). Computed
  statically from geometry; no radio faults.
* **resilience_term** — 0.5 · valid-rate drop under anchor-outage scenarios
  relative to clean, plus 0.5 · relative RMSE inflation under outage scenarios
  (capped at 1). A layout that collapses when anchors fail scores worse even
  if its clean RMSE is fine.

Every component is reported alongside the total (``ObjectiveBreakdown``).
Weights are fixed a priori for protocol 1.0.0 and were chosen on development
scenarios only; they are not re-tuned per environment.
"""

from __future__ import annotations

from .evaluation import FAULT_SCENARIOS, SplitEvaluation
from .protocol import ObjectiveBreakdown, ObjectiveNormalization, ObjectiveWeights

INF = 1e9


def compute_objective(
    evaluation: SplitEvaluation,
    weights: ObjectiveWeights | None = None,
    normalization: ObjectiveNormalization | None = None,
) -> ObjectiveBreakdown:
    weights = weights or ObjectiveWeights()
    normalization = normalization or ObjectiveNormalization()

    pooled = evaluation.pooled
    scenarios = evaluation.per_scenario

    rmse = pooled.rmse if pooled.rmse is not None else INF
    p95 = pooled.p95 if pooled.p95 is not None else INF
    error_term = weights.error * (rmse / normalization.rmse_reference_m)
    tail_term = weights.tail * (p95 / normalization.p95_reference_m)
    unavailable_term = weights.unavailable * (1.0 - pooled.valid_rate)
    geometry_term = weights.geometry * pooled.poor_geometry_fraction

    clean = scenarios.get("clean")
    fault_valid_rates = [
        scenarios[name].valid_rate for name in FAULT_SCENARIOS if name in scenarios
    ]
    fault_rmses = [
        scenarios[name].rmse
        for name in FAULT_SCENARIOS
        if name in scenarios and scenarios[name].rmse
    ]
    valid_drop = 0.0
    error_inflation = 0.0
    if fault_valid_rates:
        reference = clean.valid_rate if clean is not None else 1.0
        valid_drop = max(0.0, reference - min(fault_valid_rates))
    if fault_rmses and clean is not None and clean.rmse:
        error_inflation = min(1.0, max(0.0, max(fault_rmses) / clean.rmse - 1.0))
    resilience_term = weights.resilience * (0.5 * valid_drop + 0.5 * error_inflation)

    total = error_term + tail_term + unavailable_term + geometry_term + resilience_term
    return ObjectiveBreakdown(
        total=total,
        error_term=error_term,
        tail_term=tail_term,
        unavailable_term=unavailable_term,
        geometry_term=geometry_term,
        resilience_term=resilience_term,
        raw={
            "rmse_m": rmse if rmse < INF else -1.0,
            "p95_m": p95 if p95 < INF else -1.0,
            "valid_rate": pooled.valid_rate,
            "update_rate": pooled.update_rate,
            "poor_geometry_fraction": pooled.poor_geometry_fraction,
            "observability": pooled.observability,
            "outage_valid_drop": valid_drop,
            "outage_error_inflation": error_inflation,
        },
    )
