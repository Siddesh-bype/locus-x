import json

import numpy as np
import pytest

from backend.locus_x import policy_v2


def candidate(**overrides):
    value = {
        "id": "test",
        "mode": "hard",
        "threshold": 0.05,
        "floor": 0.05,
        "fallback": "min3",
    }
    value.update(overrides)
    return value


def test_grid_is_small_and_contains_current_policy():
    grid = policy_v2.candidate_grid()
    assert len(grid) == 21
    assert policy_v2.CURRENT_POLICY_ID in {item["id"] for item in grid}
    assert sum(item["mode"] == "soft" for item in grid) == 1


def test_p_los_conversion_and_hard_weights():
    weights, fallback, changed = policy_v2.policy_weights(
        np.asarray([0.0, 0.96, 0.0, 0.0]), 4, candidate()
    )
    assert fallback is False
    assert changed is True
    assert weights == {"0": 1.0, "1": pytest.approx(0.81), "2": 1.0, "3": 1.0}

    # Majority NLOS no longer disarms the AI: predicted-NLOS anchors get the floor weight.
    weights, fallback, changed = policy_v2.policy_weights(
        np.asarray([0.9, 0.99, 0.99]), 3, candidate()
    )
    assert weights == {"0": 0.05, "1": 0.05, "2": 0.05}
    assert fallback is False
    assert changed is True


def test_low_fallback_requires_four_anchors():
    probabilities = np.asarray([0.99, 0.0, 0.0])
    weights, fallback, _ = policy_v2.policy_weights(
        probabilities, 3, candidate(fallback="min2_if4")
    )
    # With only 3 anchors and 2 predicted LOS the majority branch applies, not a full fallback.
    assert weights == {"0": 0.05, "1": 1.0, "2": 1.0} and fallback is False
    weights, fallback, _ = policy_v2.policy_weights(
        np.asarray([0.99, 0.0, 0.0, 0.0]), 4, candidate(fallback="min2_if4")
    )
    assert weights is not None and fallback is False
    # Fewer than three anchors is the only remaining hard fallback.
    weights, fallback, _ = policy_v2.policy_weights(np.asarray([0.1, 0.2]), 2, candidate())
    assert weights is None and fallback is True


def test_anchor_count_scaling_tightens_threshold_only_when_configured():
    probabilities = np.asarray([0.0, 0.4, 0.0, 0.0])  # p_los 0.6 on anchor 1
    plain, _, _ = policy_v2.policy_weights(probabilities, 4, candidate(threshold=0.5))
    assert plain["1"] == 1.0  # 0.6 >= 0.5
    scaled, _, _ = policy_v2.policy_weights(
        probabilities, 4, candidate(threshold=0.5, anchor_scaling_reference=8)
    )
    # effective threshold = min(1, 0.5 * 8 / 4) = 1.0, so 0.6 is now downweighted
    assert scaled["1"] == pytest.approx(0.05 + 0.95 * 0.6)


def test_soft_policy_is_max_floor_probability():
    weights, fallback, _ = policy_v2.policy_weights(
        np.asarray([0.0, 0.01, 0.5]), 3, candidate(mode="soft")
    )
    assert fallback is False
    assert weights == {"0": pytest.approx(1.0), "1": pytest.approx(0.99), "2": pytest.approx(0.5)}


def test_policy_hash_and_load(tmp_path):
    path = tmp_path / "policy.json"
    policy = {
        "policy_version": "2.0.0",
        "policy_id": "hard_t005_f005_min3",
        "mode": "hard",
        "floor": 0.05,
        "threshold": 0.05,
        "fallback_min_predicted_los": 3,
        "fallback_min_predicted_los_when_4plus": 3,
    }
    policy["policy_sha256"] = policy_v2.policy_hash(policy)
    path.write_text(json.dumps(policy), encoding="utf-8")
    assert policy_v2.load_policy(path)["policy_id"] == "hard_t005_f005_min3"
    policy["floor"] = 0.2
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        policy_v2.load_policy(path)


def test_ai_path_does_not_read_simulator_configuration():
    from pathlib import Path

    from backend.locus_x import ai_policy, session

    for module in (ai_policy, session):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "settings.nlos_bias" not in source
        assert "walls_enabled" not in source


def test_frozen_policy_is_weights_plus_learned_range_correction():
    policy = policy_v2.load_policy()
    assert policy["range_correction"]["enabled"] is True
    # The ablation found both of these ineffective or harmful, so they must stay out.
    assert "anchor_count_scaling" not in policy
    assert "tracker_gate" not in policy


def test_majority_nlos_frame_keeps_default_gate_and_applies_correction():
    from types import SimpleNamespace

    from backend.locus_x.ai_policy import candidate_from_policy, run_v2_policy
    from backend.locus_x.environment import load_environments, resolve_floor
    from backend.locus_x.estimators import solve
    from backend.locus_x.schemas import AIMetadata, AIPrediction, Settings
    from backend.locus_x.simulation import generate_measurements
    from backend.locus_x.tracking import DEFAULT_GATE_CHI2

    environment = resolve_floor(load_environments()["hospital_floor_01"], None)
    settings = Settings(scenario="nlos", route="held_out")
    measurements, _ = generate_measurements(environment, settings, (10.0, 8.0), 1.0, 2)
    ids = [m.anchor_id for m in measurements]
    metadata = AIMetadata(
        status="active",
        reason="stub",
        model_version="stub",
        feature_version="stub",
        predictions=[AIPrediction(anchor_id=i, p_los=0.05, weight=1.0) for i in ids],
    )
    model = SimpleNamespace(predict=lambda *a, **k: SimpleNamespace(metadata=metadata))
    policy = policy_v2.load_policy()
    result = run_v2_policy(
        v2_model=model,
        policy=policy,
        candidate=candidate_from_policy(policy),
        measurements=measurements,
        environment=environment,
        stat_fix=solve(measurements, robust=True),
        v2_history=None,
        tracker_state=None,
        tracker_timestamp=None,
        bias_by_anchor={i: 1.5 for i in ids},
    )
    assert result.weights is not None and result.fallback is False
    assert result.gate == DEFAULT_GATE_CHI2
    assert result.corrections
    assert all(v == pytest.approx(0.95 * 1.5) for v in result.corrections.values())
    assert all(p.weight == pytest.approx(0.08) for p in result.metadata.predictions)
