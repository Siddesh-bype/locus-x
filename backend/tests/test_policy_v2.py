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

    weights, fallback, changed = policy_v2.policy_weights(
        np.asarray([0.9, 0.99, 0.99]), 3, candidate()
    )
    assert weights is None
    assert fallback is True
    assert changed is False


def test_low_fallback_requires_four_anchors():
    probabilities = np.asarray([0.99, 0.0, 0.0])
    weights, fallback, _ = policy_v2.policy_weights(
        probabilities, 3, candidate(fallback="min2_if4")
    )
    assert weights is None and fallback is True
    weights, fallback, _ = policy_v2.policy_weights(
        np.asarray([0.99, 0.0, 0.0, 0.0]), 4, candidate(fallback="min2_if4")
    )
    assert weights is not None and fallback is False


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
