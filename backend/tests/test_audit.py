"""Integration invariants verified by the final audit, including adverse input."""

from copy import deepcopy

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.locus_x import estimators
from backend.locus_x import session as session_module
from backend.locus_x.environment import load_environments
from backend.locus_x.main import app
from backend.locus_x.schemas import Settings
from backend.locus_x.session import SimulationSession

client = TestClient(app, raise_server_exceptions=False)


def assert_errors(frame):
    truth = np.array(frame["evaluation"]["ground_truth"])
    for result_key, error_key in (("baseline", "baseline_error"), ("tracking", "proposed_error")):
        position = frame[result_key]["position"]
        error = frame["evaluation"][error_key]
        if position is None:
            assert error is None
        else:
            assert error == pytest.approx(float(np.linalg.norm(np.array(position) - truth)))


@pytest.mark.parametrize("bad_scenario", [[], {}, ["clean"], 123])
def test_bad_scenario_is_validation_error_not_server_error(bad_scenario):
    response = client.post("/api/v1/sessions", json={"settings": {"scenario": bad_scenario}})
    assert response.status_code == 422


def test_duplicate_anchors_rejected_without_changing_session():
    duplicates = ["A1"] * 10
    assert (
        client.post(
            "/api/v1/sessions",
            json={
                "settings": {
                    "disabled_anchors": duplicates,
                }
            },
        ).status_code
        == 422
    )
    frame = client.post("/api/v1/sessions", json={}).json()
    path = f"/api/v1/sessions/{frame['session_id']}"
    response = client.patch(
        f"{path}/controls",
        json={
            "expected_revision": frame["revision"],
            "disabled_anchors": duplicates,
        },
    )
    assert response.status_code == 422
    assert client.get(path).json() == frame
    client.delete(path)


def test_fault_controls_regenerate_ranges_and_both_estimators():
    frame = client.post("/api/v1/sessions", json={"settings": {"scenario": "clean"}}).json()
    path = f"/api/v1/sessions/{frame['session_id']}"
    for changes in (
        {"noise_std": 0.8},
        {"disabled_anchors": ["A1"]},
        {"nlos_bias": 5},
        {"walls_enabled": False},
        {"scenario": "chaos"},
    ):
        previous = frame
        response = client.patch(
            f"{path}/controls",
            json={
                "expected_revision": previous["revision"],
                **changes,
            },
        )
        assert response.status_code == 200
        frame = response.json()
        assert frame["revision"] == previous["revision"] + 1
        assert frame["timestamp"] == previous["timestamp"]
        assert frame["measurements"] != previous["measurements"]
        for method in ("baseline", "robust"):
            assert frame[method]["position"] != previous[method]["position"]
            assert set(frame[method]["residuals"]) == {
                measurement["anchor_id"] for measurement in frame["measurements"]
            }
        assert_errors(frame)
        if changes.get("scenario") == "chaos":
            assert frame["settings"]["walls_enabled"] is True
            assert frame["evaluation"]["has_nlos"] is True
    client.delete(path)


@pytest.mark.parametrize(
    "initial",
    [
        {},
        {
            "scenario": "noise",
            "seed": 44,
            "route": "held_out",
            "speed": 2,
            "disabled_anchors": ["A1", "A3"],
            "walls_enabled": False,
        },
    ],
)
def test_reset_restores_creation_settings_route_anchor_states_and_ranges(initial):
    original = client.post("/api/v1/sessions", json={"settings": initial}).json()
    frame = original
    path = f"/api/v1/sessions/{frame['session_id']}"
    for changes in (
        {"scenario": "chaos", "seed": 99, "route": "held_out", "speed": 3},
        {"disabled_anchors": ["A2", "A4", "A6", "A8"], "walls_enabled": False},
    ):
        frame = client.patch(
            f"{path}/controls",
            json={
                "expected_revision": frame["revision"],
                **changes,
            },
        ).json()
    frame = client.post(
        f"{path}/advance",
        json={
            "expected_revision": frame["revision"],
            "dt": 1,
        },
    ).json()
    restored = client.post(f"{path}/reset", json={"expected_revision": frame["revision"]}).json()
    assert restored["revision"] > frame["revision"]
    for key in (
        "settings",
        "timestamp",
        "frame_index",
        "measurements",
        "simulator_labels",
        "evaluation",
    ):
        assert restored[key] == original[key]
    assert len(restored["events"]) == 1
    assert restored["tracking"]["covariance"] == original["tracking"]["covariance"]
    client.delete(path)


def test_insufficient_and_collinear_api_geometry_never_fabricates_a_fix():
    original = client.post("/api/v1/sessions", json={"settings": {"scenario": "clean"}}).json()
    frame = original
    path = f"/api/v1/sessions/{frame['session_id']}"
    # A1/A2/A3 all lie on the south wall: three ranges do not imply 2-D geometry.
    frame = client.patch(
        f"{path}/controls",
        json={
            "expected_revision": frame["revision"],
            "disabled_anchors": ["A4", "A5", "A6", "A7", "A8"],
        },
    ).json()
    assert len(frame["measurements"]) == 3
    for method in ("baseline", "robust"):
        assert frame[method]["status"] == "poor_geometry"
        assert frame[method]["position"] is None
    assert frame["tracking"]["status"] == "unavailable"  # Recomputed initial frame, no prior.
    restored = client.post(f"{path}/reset", json={"expected_revision": frame["revision"]}).json()
    frame = client.post(f"{path}/advance", json={"expected_revision": restored["revision"]}).json()
    frame = client.patch(
        f"{path}/controls",
        json={
            "expected_revision": frame["revision"],
            "dropout": 1,
        },
    ).json()
    assert not frame["measurements"]
    assert frame["tracking"]["status"] == "degraded"
    initial_covariance = np.trace(frame["tracking"]["covariance"])
    frame = client.post(
        f"{path}/advance",
        json={
            "expected_revision": frame["revision"],
            "dt": 1,
        },
    ).json()
    assert np.trace(frame["tracking"]["covariance"]) > initial_covariance
    for _ in range(3):
        frame = client.post(
            f"{path}/advance",
            json={
                "expected_revision": frame["revision"],
                "dt": 1,
            },
        ).json()
    assert frame["tracking"]["status"] == "unavailable"
    assert frame["tracking"]["position"] is None
    assert frame["tracking"]["ellipse"] is None
    assert_errors(frame)
    client.delete(path)


def test_truth_and_fault_labels_cannot_change_estimates_for_identical_observations(monkeypatch):
    environment = load_environments()["hospital_floor_01"]
    original = SimulationSession(environment, Settings())
    measurements = original.frame.measurements
    labels = original.frame.simulator_labels
    inputs = []
    actual_solve = session_module.solve

    def spy(observations, **kwargs):
        inputs.append(deepcopy(observations))
        return actual_solve(observations, **kwargs)

    monkeypatch.setattr(session_module, "solve", spy)
    monkeypatch.setattr(
        session_module, "generate_measurements", lambda *args: (measurements, labels)
    )
    first = SimulationSession(environment, Settings()).frame
    monkeypatch.setattr(session_module, "route_position", lambda *args: (200, 200))
    fake_labels = [
        label.model_copy(
            update={"true_range": 999, "injected_bias": 888, "intersected_walls": [], "kind": "los"}
        )
        for label in labels
    ]
    monkeypatch.setattr(
        session_module, "generate_measurements", lambda *args: (measurements, fake_labels)
    )
    second = SimulationSession(environment, Settings()).frame
    assert inputs[0] == inputs[1] == inputs[2] == inputs[3]
    for key in ("baseline", "robust", "tracking", "ai", "ai_tracking", "ai_metadata"):
        assert getattr(first, key).model_dump(exclude={"runtime_ms"}) == getattr(
            second, key
        ).model_dump(exclude={"runtime_ms"})
    assert first.ai_metadata.predictions == second.ai_metadata.predictions
    assert first.ai.reliability == second.ai.reliability
    assert first.evaluation.baseline_error != second.evaluation.baseline_error
    assert first.evaluation.proposed_error != second.evaluation.proposed_error
    assert first.evaluation.ai_error != second.evaluation.ai_error
    assert_errors(second.model_dump())


@pytest.mark.parametrize("operation", ["svd", "lstsq"])
def test_initialization_numerical_failure_is_an_unavailable_result(monkeypatch, operation):
    environment = load_environments()["hospital_floor_01"]
    frame = SimulationSession(environment, Settings()).frame

    def failure(*args, **kwargs):
        raise np.linalg.LinAlgError("Numerical failure")

    monkeypatch.setattr(estimators.np.linalg, operation, failure)
    for robust in (False, True):
        result = estimators.solve(frame.measurements, robust=robust)
        assert result.position is None
        assert result.status == "solver_failed"
