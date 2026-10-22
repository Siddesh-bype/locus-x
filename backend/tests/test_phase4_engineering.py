from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.locus_x import reliability_v2
from backend.locus_x.ai_phase3 import evaluate_frames, load_frames
from backend.locus_x.environment import load_environments, resolve_floor, route_position
from backend.locus_x.estimators import solve
from backend.locus_x.policy_v2 import load_policy, policy_hash, policy_weights
from backend.locus_x.reliability_v2 import (
    FEATURE_NAMES_V2,
    FeatureHistoryV2,
    ReliabilityV2Model,
)
from backend.locus_x.schemas import Measurement, Settings
from backend.locus_x.simulation import generate_measurements

ROOT = Path(__file__).resolve().parents[2]
V2_MODEL = ROOT / "models" / "ai-reliability-v2"


def _copy_model(tmp_path: Path) -> Path:
    target = tmp_path / "model"
    shutil.copytree(V2_MODEL, target)
    return target


def _measurements() -> tuple[object, list[Measurement]]:
    environment = resolve_floor(load_environments()["hospital_floor_01"], None)
    truth = route_position(environment.routes["held_out"], 0.0, 1.2)
    observations, _ = generate_measurements(
        environment,
        Settings(scenario="clean", seed=8001, route="held_out"),
        truth,
        0.0,
        0,
    )
    return environment, observations


def test_load_rejects_corrupt_checkpoint_checksum(tmp_path):
    path = _copy_model(tmp_path)
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    config["checkpoints"][0]["sha256"] = "0" * 64
    (path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    model = ReliabilityV2Model.load(path)
    assert not model.available
    assert "checksum" in (model.error or "")


def test_load_rejects_unsupported_schema_version(tmp_path):
    path = _copy_model(tmp_path)
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    config["feature_version"] = "9.9.9"
    (path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    model = ReliabilityV2Model.load(path)
    assert not model.available
    assert "feature version" in (model.error or "")


def test_missing_model_and_nonfinite_features_fallback():
    missing = ReliabilityV2Model.load(Path("definitely-missing-model"))
    environment, observations = _measurements()
    history = FeatureHistoryV2()
    output = missing.predict(observations, environment, solve(observations), history)
    assert output.weights is None
    assert output.metadata.status == "fallback"
    assert "unavailable" in output.metadata.reason


def test_nonfinite_v2_features_fallback(monkeypatch):
    model = ReliabilityV2Model.load(V2_MODEL)
    assert model.available
    environment, observations = _measurements()
    batch = SimpleNamespace(
        anchors=[m.anchor_id for m in observations],
        matrix=np.full((len(observations), len(FEATURE_NAMES_V2)), np.nan),
    )
    monkeypatch.setattr(reliability_v2, "extract_features_v2", lambda *args, **kwargs: batch)
    output = model.predict(
        observations, environment, solve(observations), FeatureHistoryV2()
    )
    assert output.weights is None
    assert output.metadata.status == "fallback"
    assert "non-finite" in output.metadata.reason


def test_all_anchors_lost_fallback():
    model = ReliabilityV2Model.load(V2_MODEL)
    environment, _ = _measurements()
    output = model.predict([], environment, None, FeatureHistoryV2())
    assert output.weights is None
    assert output.metadata.status == "fallback"
    assert "features unavailable" in output.metadata.reason


def test_policy_fallback_for_missing_or_nonfinite_probabilities():
    candidate = {
        "threshold": 0.05,
        "floor": 0.05,
        "fallback": "min3",
        "mode": "hard",
    }
    assert policy_weights(np.array([0.1, 0.2]), 2, candidate)[:2] == (None, True)
    assert policy_weights(np.array([0.1, np.nan, 0.2]), 3, candidate)[:2] == (None, True)
    weights, fallback, changed = policy_weights(
        np.array([0.05, 0.05, 0.05]), 3, candidate
    )
    assert fallback is False and changed is False
    assert all(value == pytest.approx(1.0) for value in weights.values())


def test_policy_file_checksum_round_trip(tmp_path):
    policy = load_policy()
    assert policy_hash(policy) == policy["policy_sha256"]
    broken = tmp_path / "policy.json"
    payload = dict(policy)
    payload["threshold"] = 0.123456
    broken.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        load_policy(broken)


def test_probability_conversion_uses_one_minus_p_nlos(tmp_path, monkeypatch):
    path = _copy_model(tmp_path)
    checkpoint = next(path.glob("seed_*.npz"))
    with np.load(checkpoint, allow_pickle=False) as arrays:
        content = {name: np.array(arrays[name]) for name in arrays.files}
    content["b3"] = np.full_like(content["b3"], 10.0)
    np.savez_compressed(checkpoint, **content)
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    from backend.locus_x.reliability_v2 import _sha256

    config["checkpoints"][0]["sha256"] = _sha256(checkpoint)
    (path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    model = ReliabilityV2Model.load(path)
    environment, observations = _measurements()
    matrix = np.tile(model.checkpoints[0]["mean"], (len(observations), 1))
    batch = SimpleNamespace(
        anchors=[m.anchor_id for m in observations], matrix=matrix
    )
    monkeypatch.setattr(reliability_v2, "extract_features_v2", lambda *args, **kwargs: batch)
    output = model.predict(
        observations, environment, solve(observations), FeatureHistoryV2()
    )
    nlos = model._probabilities(matrix)
    np.testing.assert_allclose(
        [p.p_los for p in output.metadata.predictions], 1.0 - nlos
    )


def test_truth_and_labels_do_not_change_ai_policy_decisions():
    frames, manifest = load_frames("validation")
    run_id = int(manifest["run_catalog"][0]["run_id"])
    indices = np.flatnonzero(frames["run_id"] == run_id)[:120]
    subset = {key: np.asarray(value[indices]) for key, value in frames.items()}
    subset["run_id"] = np.zeros(len(indices), dtype=int)
    manifest = dict(manifest)
    manifest["run_catalog"] = [dict(manifest["run_catalog"][0], run_id=0)]

    def probabilities(matrix):
        return np.full(len(matrix), 0.95)

    first = evaluate_frames(probabilities, subset, manifest, model_name="test")
    changed = {key: value.copy() for key, value in subset.items()}
    changed["truth"] = np.zeros_like(changed["truth"])
    changed["n_nlos"] = np.zeros_like(changed["n_nlos"])
    changed["has_dropout"] = np.zeros_like(changed["has_dropout"])
    changed["has_outage"] = np.zeros_like(changed["has_outage"])
    second = evaluate_frames(probabilities, changed, manifest, model_name="test")
    assert np.array_equal(first["ambiguous"], second["ambiguous"])
    assert np.array_equal(first["predicted_nlos"], second["predicted_nlos"])
