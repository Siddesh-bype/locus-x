import inspect
import json

import numpy as np
import pytest
from pydantic import ValidationError

from backend.locus_x import estimators, tracking
from backend.locus_x.benchmark import run_benchmark
from backend.locus_x.environment import intersects, load_environments, route_position
from backend.locus_x.estimators import solve
from backend.locus_x.schemas import BenchmarkRequest, Controls, Estimate, Measurement, Settings
from backend.locus_x.session import SimulationSession
from backend.locus_x.simulation import generate_measurements
from backend.locus_x.tracking import CHI2_95_2D, MotionTracker, covariance_ellipse


@pytest.fixture
def environment():
    return load_environments()["hospital_floor_01"]


def observations(position=(3, 4), anchors=((0, 0), (10, 0), (10, 10), (0, 10))):
    return [
        Measurement(
            anchor_id=str(i),
            anchor_position=a,
            timestamp=0,
            observed_range=float(np.linalg.norm(np.array(position) - a)),
        )
        for i, a in enumerate(anchors)
    ]


def stable_frame(frame):
    data = frame.model_dump()
    for key in ("session_id", "revision", "events"):
        data.pop(key)
    for key in ("baseline", "robust", "tracking"):
        data[key].pop("runtime_ms")
    for key in ("ai", "ai_tracking", "ai_metadata"):
        data.pop(key, None)
    return data


@pytest.mark.parametrize("robust", [False, True])
def test_exact_geometric_ranges_and_solution(robust):
    measurements = observations()
    assert measurements[0].observed_range == 5
    result = solve(measurements, robust=robust)
    assert result.status == "valid"
    np.testing.assert_allclose(result.position, (3, 4), atol=1e-6)
    assert max(abs(v) for v in result.residuals.values()) < 1e-6
    assert np.linalg.eigvalsh(result.covariance).min() > 0


def test_noisy_estimation():
    measurements = observations()
    rng = np.random.default_rng(22)
    noisy = [
        m.model_copy(update={"observed_range": m.observed_range + rng.normal(0, 0.1)})
        for m in measurements
    ]
    assert np.linalg.norm(np.array(solve(noisy).position) - (3, 4)) < 0.3


@pytest.mark.parametrize("count", [0, 1, 2])
def test_insufficient(count):
    assert solve(observations()[:count]).status == "insufficient"


def test_degenerate_and_invalid():
    assert solve(observations(anchors=((0, 0), (5, 0), (10, 0)))).status == "poor_geometry"
    assert solve(observations(anchors=((0, 0), (5, 0.001), (10, 0)))).position is None
    valid = observations()[:2]
    invalid = Measurement.model_construct(
        anchor_id="bad", anchor_position=(0, 5), timestamp=0, observed_range=float("nan")
    )
    assert solve(valid + [invalid, valid[0]]).status == "insufficient"
    with pytest.raises(ValidationError):
        Measurement(anchor_id="bad", anchor_position=(0, 0), timestamp=0, observed_range=-2)


def test_solver_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("solver failure")

    monkeypatch.setattr(estimators, "least_squares", fail)
    assert solve(observations()).status == "solver_failed"


def test_wall_crossing_and_door_gap(environment):
    assert intersects((0, 0), (10, 10), (0, 10), (10, 0))
    assert intersects((0, 0), (10, 0), (5, 0), (15, 0))
    assert not intersects((0, 0), (1, 1), (2, 0), (2, 4))
    assert not any(intersects((4, 6), (4, 8), w.a, w.b) for w in environment.walls)
    assert any(intersects((6, 6), (6, 8), w.a, w.b) for w in environment.walls)


def test_nlos_positive_and_toggle(environment):
    settings = Settings(noise_std=0, nlos_bias=3)
    measurements, labels = generate_measurements(environment, settings, (4, 10), 0, 0)
    assert any(label.injected_bias > 0 for label in labels)
    for m, label in zip(measurements, labels, strict=True):
        assert m.observed_range == pytest.approx(label.true_range + label.injected_bias)
        assert label.injected_bias >= 0
    clean, _ = generate_measurements(
        environment, settings.model_copy(update={"walls_enabled": False}), (4, 10), 0, 0
    )
    assert sum(m.observed_range for m in clean) < sum(m.observed_range for m in measurements)


def test_determinism_noise_dropout_availability(environment):
    settings = Settings(seed=123, dropout=0.3)
    args = environment, settings, (4, 10), 1.0, 5
    assert generate_measurements(*args) == generate_measurements(*args)
    other, _ = generate_measurements(
        environment, settings.model_copy(update={"seed": 124}), (4, 10), 1, 5
    )
    assert other != generate_measurements(*args)[0]
    none, labels = generate_measurements(
        environment, settings.model_copy(update={"dropout": 1}), (4, 10), 1, 5
    )
    assert not none and all(not label.available for label in labels)
    full = Settings(noise_std=0.2, nlos_bias=0)
    all_measurements, _ = generate_measurements(environment, full, (4, 10), 1, 5)
    fewer, _ = generate_measurements(
        environment, full.model_copy(update={"disabled_anchors": ["A1"]}), (4, 10), 1, 5
    )
    assert all_measurements[1:] == fewer
    clean, _ = generate_measurements(
        environment, full.model_copy(update={"noise_std": 0}), (4, 10), 1, 5
    )
    assert any(
        a.observed_range != b.observed_range for a, b in zip(all_measurements, clean, strict=True)
    )


def test_timed_outage_and_recovery(environment):
    settings = Settings(scenario="anchor_failure", noise_std=0, nlos_bias=0)
    for timestamp, expected in ((11, 8), (12, 4), (21, 4), (22, 8)):
        measurements, _ = generate_measurements(
            environment, settings, (4, 10), timestamp, timestamp
        )
        assert len(measurements) == expected


def test_route_stops_turns_and_stays_inside(environment):
    route = environment.routes["asset_round"]
    assert route_position(route, 0, 1) == route_position(route, 1, 1)
    assert route_position(route, 5, 1) != route_position(route, 1, 1)
    for t in np.linspace(0, 150, 301):
        x, y = route_position(route, t, 1.2)
        assert 0 <= x <= environment.width and 0 <= y <= environment.height
    # Every route segment passes through declared door gaps, never through a wall.
    for route in environment.routes.values():
        for i, waypoint in enumerate(route):
            end = route[(i + 1) % len(route)].position
            assert not any(intersects(waypoint.position, end, w.a, w.b) for w in environment.walls)


def test_reset_and_control_recompute(environment):
    session = SimulationSession(environment, Settings())
    initial = stable_frame(session.frame)
    for _ in range(15):
        session.advance()
    assert stable_frame(session.reset()) == initial
    before = session.frame
    after = session.update(Controls(expected_revision=session.revision, nlos_bias=7))
    assert after.timestamp == before.timestamp
    assert after.measurements != before.measurements
    assert after.baseline.position != before.baseline.position
    assert after.revision > before.revision
    # Idempotent controls do not reduce covariance by applying the same measurement twice.
    repeated = session.update(Controls(expected_revision=session.revision, nlos_bias=7))
    assert repeated.tracking.covariance == after.tracking.covariance


def test_filter_outage_covariance_and_reacquisition():
    tracker = MotionTracker()
    fix = solve(observations(), robust=True)
    first = tracker.update(fix, 0)
    missing = Estimate(status="insufficient", reason="Only two anchors")
    coast = tracker.update(missing, 1)
    assert coast.status == "degraded" and coast.position is not None
    assert np.trace(coast.covariance) > np.trace(first.covariance)
    unavailable = tracker.update(missing, 3)
    assert unavailable.status == "unavailable" and unavailable.position is None
    assert tracker.update(fix, 4).status == "tracking"


def test_ellipse_confidence_scaling():
    ellipse = covariance_ellipse(np.diag([4, 1]))
    assert ellipse.major == pytest.approx(np.sqrt(CHI2_95_2D * 4))
    assert ellipse.minor == pytest.approx(np.sqrt(CHI2_95_2D))


def test_input_boundary_and_truth_isolation(environment, monkeypatch):
    assert set(Measurement.model_fields) == {
        "anchor_id",
        "anchor_position",
        "timestamp",
        "observed_range",
    }
    for module in (estimators, tracking):
        source = inspect.getsource(module)
        assert "from .simulation" not in source and "from .evaluation" not in source
    import backend.locus_x.session as session_module

    actual = session_module.solve
    inputs = []

    def spy(measurements, **kwargs):
        inputs.append(measurements)
        return actual(measurements, **kwargs)

    monkeypatch.setattr(session_module, "solve", spy)
    SimulationSession(environment, Settings())
    assert inputs[0] is inputs[1]
    result = actual(inputs[0], robust=True)
    monkeypatch.setattr(session_module, "route_position", lambda *args: (999, 999))
    assert actual(inputs[0], robust=True).position == result.position


def test_benchmark_reproducibility():
    config = BenchmarkRequest(
        scenarios=["clean", "chaos"], anchor_counts=[3, 8], seeds=[777], steps=10
    )
    first, second = run_benchmark(config), run_benchmark(config)
    assert first["traces"] == second["traces"]
    assert first["distributions"] == second["distributions"]
    for a, b in zip(first["rows"], second["rows"], strict=True):
        a.pop("latency_ms")
        b.pop("latency_ms")
        assert a == b
    json.dumps(first, allow_nan=False)
