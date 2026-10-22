import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from backend.locus_x import reliability_v2
from backend.locus_x.environment import load_environments
from backend.locus_x.estimators import solve
from backend.locus_x.reliability import FeatureHistory, extract_features
from backend.locus_x.reliability_v2 import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    G1_MISSINGNESS,
    G4_TRACKER,
    G5_RECEPTION,
    FeatureHistoryV2,
    ReliabilityV2Model,
    extract_features_v2,
)
from backend.locus_x.schemas import Environment, Measurement


@pytest.fixture
def environment() -> Environment:
    return load_environments()["hospital_floor_01"]


def make_measurements(
    environment: Environment,
    position: tuple[float, float],
    timestamp: float,
    anchor_ids: tuple[str, ...] = ("A1", "A3", "A5", "A7"),
    common_bias: float = 0.0,
    noise: float = 0.0,
    seed: int = 1,
) -> list[Measurement]:
    rng = np.random.default_rng(seed)
    by_id = {anchor.id: anchor for anchor in environment.anchors}
    output = []
    for anchor_id in anchor_ids:
        anchor = by_id[anchor_id]
        true_range = float(np.linalg.norm(np.asarray(position) - anchor.position))
        output.append(
            Measurement(
                anchor_id=anchor_id,
                anchor_position=anchor.position,
                timestamp=timestamp,
                observed_range=max(1e-6, true_range + common_bias + rng.normal(0, noise)),
            )
        )
    return output


def column(name: str) -> int:
    return FEATURE_NAMES_V2.index(name)


def make_checkpoint(scale: float = 1.0) -> dict[str, np.ndarray]:
    width = len(FEATURE_NAMES_V2)
    return {
        "mean": np.zeros(width),
        "scale": np.full(width, scale),
        "calibration": np.asarray([1.0, 0.0]),
        "W1": np.zeros((width, 4)),
        "b1": np.zeros(4),
        "W2": np.zeros((4, 4)),
        "b2": np.zeros(4),
        "W3": np.zeros((4, 1)),
        "b3": np.zeros(1),
    }


def write_model(directory: Path, checkpoint: dict[str, np.ndarray] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = checkpoint or make_checkpoint()
    file_path = directory / "seed_1.npz"
    np.savez_compressed(file_path, **checkpoint)
    config = {
        "feature_version": reliability_v2.FEATURE_VERSION_V2,
        "model_version": reliability_v2.MODEL_VERSION_V2,
        "feature_names": FEATURE_NAMES_V2,
        "checkpoints": [
            {"file": file_path.name, "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest()}
        ],
        "weight_policy": {"floor": 0.05, "threshold": 0.05, "ambiguous_predicted_los": 3},
    }
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return directory


def test_g0_is_exact_v1_reproduction(environment):
    v1_history = FeatureHistory()
    v2_history = FeatureHistoryV2()
    for step in range(5):
        timestamp = step * 0.5
        measurements = make_measurements(
            environment, (4 + step * 0.2, 10), timestamp, noise=0.1, seed=step
        )
        estimate = solve(measurements)
        v1 = extract_features(measurements, environment, estimate, v1_history)
        v2 = extract_features_v2(measurements, environment, estimate, v2_history)
        assert v1 is not None and v2 is not None
        np.testing.assert_allclose(v2.matrix[:, : len(FEATURE_NAMES)], v1.matrix, atol=1e-12)


def test_causality_and_missingness_are_explicit(environment):
    history = FeatureHistoryV2()
    first = make_measurements(environment, (4, 10), 0.0)
    batch = extract_features_v2(first, environment, solve(first), history)
    assert batch is not None
    gap = column("frames_since_last_observation_norm")
    has_previous = column("has_previous_observation")
    missing = column("previous_residual_missing")
    assert np.all(batch.matrix[:, has_previous] == 0)
    assert np.all(batch.matrix[:, gap] == 1)
    assert np.all(batch.matrix[:, missing] == 1)

    second = make_measurements(environment, (4.2, 10), 0.5)
    batch = extract_features_v2(second, environment, solve(second), history)
    assert np.all(batch.matrix[:, has_previous] == 1)
    assert np.all(batch.matrix[:, gap] == pytest.approx(1 / reliability_v2.FRAME_GAP_CAP))
    assert np.all(batch.matrix[:, missing] == 0)

    third = make_measurements(environment, (4.4, 10), 1.0, anchor_ids=("A1", "A3", "A5"))
    assert extract_features_v2(third, environment, solve(third), history) is not None
    fourth = make_measurements(environment, (4.6, 10), 1.5)
    batch = extract_features_v2(fourth, environment, solve(fourth), history)
    a7 = batch.anchors.index("A7")
    assert batch.matrix[a7, gap] == pytest.approx(2 / reliability_v2.FRAME_GAP_CAP)
    assert batch.matrix[a7, missing] == 1


def test_range_history_and_available_anchor_count(environment):
    history = FeatureHistoryV2()
    first = make_measurements(environment, (4, 10), 0.0)
    extract_features_v2(first, environment, solve(first), history)
    changed = [
        measurement.model_copy(update={"observed_range": measurement.observed_range + 1.0})
        for measurement in first
    ]
    changed = [
        measurement.model_copy(update={"timestamp": 0.5}) for measurement in changed
    ]
    batch = extract_features_v2(changed, environment, solve(changed), history)
    assert batch is not None
    row = 0
    assert batch.matrix[row, column("range_last_diff_norm")] == pytest.approx(
        1.0 / np.hypot(environment.width, environment.height)
    )
    assert batch.matrix[row, column("range_rate_norm")] == pytest.approx(1.0 / 1.5)
    assert batch.matrix[row, column("implied_speed_ratio")] == pytest.approx(1.0 / 1.5)
    assert batch.matrix[row, column("available_anchor_count_norm")] == pytest.approx(
        4 / len(environment.anchors)
    )


def test_common_bias_is_not_visible_to_triangle_inequality(environment):
    history = FeatureHistoryV2()
    measurements = make_measurements(environment, (4, 10), 0.0, common_bias=5.0)
    batch = extract_features_v2(measurements, environment, solve(measurements), history)
    assert batch is not None
    assert np.all(batch.matrix[:, column("pair_violation_count_norm")] == 0)
    assert np.all(batch.matrix[:, column("pair_violation_magnitude_norm")] == 0)


def test_tracker_features_use_only_the_passed_prior_state(environment):
    measurements = make_measurements(environment, (4, 10), 1.0)
    estimate = solve(measurements)
    state_a = np.asarray([4.0, 10.0, 0.0, 0.0])
    state_b = np.asarray([5.0, 10.0, 0.0, 0.0])
    batch_a = extract_features_v2(
        measurements, environment, estimate, FeatureHistoryV2(), state_a, 0.5
    )
    batch_b = extract_features_v2(
        measurements, environment, estimate, FeatureHistoryV2(), state_b, 0.5
    )
    assert batch_a is not None and batch_b is not None
    tracker_columns = [column(name) for name in G4_TRACKER]
    assert not np.allclose(batch_a.matrix[:, tracker_columns], batch_b.matrix[:, tracker_columns])
    untouched = [
        index
        for index, name in enumerate(FEATURE_NAMES_V2)
        if name not in G4_TRACKER and name not in G5_RECEPTION and name not in G1_MISSINGNESS
    ]
    np.testing.assert_allclose(batch_a.matrix[:, untouched], batch_b.matrix[:, untouched])


def test_missing_model_and_corrupt_checksum_fall_back(tmp_path):
    missing = ReliabilityV2Model.load(tmp_path / "missing")
    assert missing.available is False
    measurements = make_measurements(
        load_environments()["hospital_floor_01"], (4, 10), 0.0
    )
    history = FeatureHistoryV2()
    output = missing.predict(
        measurements, load_environments()["hospital_floor_01"], solve(measurements), history
    )
    assert output.weights is None
    assert output.metadata.status == "fallback"

    directory = write_model(tmp_path / "corrupt")
    (directory / "seed_1.npz").write_bytes(b"not a model")
    corrupt = ReliabilityV2Model.load(directory)
    assert corrupt.available is False


def test_model_loads_and_nonfinite_probability_falls_back(tmp_path, monkeypatch):
    directory = write_model(tmp_path / "model")
    model = ReliabilityV2Model.load(directory)
    assert model.available is True
    environment = load_environments()["hospital_floor_01"]
    measurements = make_measurements(environment, (4, 10), 0.0)
    output = model.predict(measurements, environment, solve(measurements), FeatureHistoryV2())
    assert output.metadata.status == "active"
    assert output.weights is not None
    assert len(output.metadata.predictions) == len(measurements)

    monkeypatch.setattr(
        model,
        "_probabilities",
        lambda matrix: np.full(len(matrix), np.nan),
    )
    fallback = model.predict(
        measurements, environment, solve(measurements), FeatureHistoryV2()
    )
    assert fallback.weights is None
    assert "non-finite" in fallback.metadata.reason


def test_model_prediction_is_deterministic(tmp_path):
    model = ReliabilityV2Model.load(write_model(tmp_path / "model"))
    environment = load_environments()["hospital_floor_01"]
    measurements = make_measurements(environment, (4, 10), 0.0, noise=0.1)
    first = model.predict(measurements, environment, solve(measurements), FeatureHistoryV2())
    second = model.predict(measurements, environment, solve(measurements), FeatureHistoryV2())
    assert first.weights == second.weights
    assert first.metadata.predictions == second.metadata.predictions


def test_truth_boundary_and_feature_schema():
    source = inspect.getsource(reliability_v2)
    assert "from .simulation" not in source
    assert "from .evaluation" not in source
    assert "fault_labels" not in inspect.signature(extract_features_v2).parameters
    assert len(FEATURE_NAMES_V2) == len(set(FEATURE_NAMES_V2)) == 41
    assert FEATURE_NAMES_V2[:24] == FEATURE_NAMES
    assert set(FEATURE_NAMES_V2[24:]) == set(
        reliability_v2.G2_RANGE
        + reliability_v2.G3_CONSISTENCY
        + reliability_v2.G4_TRACKER
        + reliability_v2.G5_RECEPTION
        + reliability_v2.G1_MISSINGNESS
    )
