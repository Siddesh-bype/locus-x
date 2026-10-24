import pytest
from fastapi.testclient import TestClient

from backend.locus_x.main import app, sessions

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_sessions():
    sessions.clear()
    from backend.locus_x.main import environments as catalog
    from backend.locus_x.main import layout_jobs

    snapshot = dict(catalog)
    layout_jobs.clear()
    yield
    sessions.clear()
    layout_jobs.clear()
    catalog.clear()
    catalog.update(snapshot)


def test_health_and_environment_coordinates():
    assert client.get("/api/v1/health").json()["status"] == "ok"
    response = client.get("/api/v1/environments").json()
    assert "(x,elevation,-y)" in response["coordinate_system"]
    assert len(response["environments"][0]["anchors"]) == 8


def test_full_lifecycle_fault_and_reset():
    response = client.post(
        "/api/v1/sessions", json={"settings": {"scenario": "clean", "noise_std": 0, "nlos_bias": 0}}
    )
    assert response.status_code == 201
    frame = response.json()
    session_id, truth = frame["session_id"], frame["evaluation"]["ground_truth"]
    path = f"/api/v1/sessions/{session_id}"
    for _ in range(5):
        frame = client.post(
            f"{path}/advance", json={"expected_revision": frame["revision"], "dt": 1}
        ).json()
    assert frame["evaluation"]["ground_truth"] != truth
    assert frame["evaluation"]["baseline_error"] < 1e-5
    stale = frame["revision"] - 1
    assert client.post(f"{path}/advance", json={"expected_revision": stale}).status_code == 409
    frame = client.patch(
        f"{path}/controls", json={"expected_revision": frame["revision"], "scenario": "chaos"}
    ).json()
    assert frame["settings"]["nlos_bias"] == 4
    assert frame["evaluation"]["baseline_error"] > 0.01
    frame = client.patch(
        f"{path}/controls", json={"expected_revision": frame["revision"], "dropout": 1}
    ).json()
    assert frame["baseline"]["status"] == "insufficient"
    for _ in range(4):
        frame = client.post(
            f"{path}/advance", json={"expected_revision": frame["revision"], "dt": 1}
        ).json()
    assert frame["tracking"]["status"] == "unavailable"
    frame = client.post(f"{path}/reset", json={"expected_revision": frame["revision"]}).json()
    assert frame["timestamp"] == 0
    assert frame["evaluation"]["ground_truth"] == truth
    assert client.get(path).json() == frame
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [{"dropout": 1.1}, {"noise_std": -1}, {"route": "missing"}, {"disabled_anchors": ["unknown"]}],
)
def test_invalid_controls(payload):
    frame = client.post("/api/v1/sessions", json={}).json()
    path = f"/api/v1/sessions/{frame['session_id']}/controls"
    assert client.patch(path, json={"expected_revision": 0, **payload}).status_code == 422


def test_unknown_environment_and_extra_truth_rejected():
    assert client.post("/api/v1/sessions", json={"environment_id": "missing"}).status_code == 404
    assert client.post("/api/v1/sessions", json={"ground_truth": [1, 1]}).status_code == 422
    assert client.get("/api/v1/benchmarks/missing").status_code == 404


def test_scenario_only_initialization_uses_preset():
    frame = client.post("/api/v1/sessions", json={"settings": {"scenario": "clean"}}).json()
    assert frame["settings"]["noise_std"] == frame["settings"]["nlos_bias"] == 0
    assert frame["evaluation"]["baseline_error"] < 1e-5


def test_benchmark_job_lifecycle_and_actual_output():
    import time

    response = client.post(
        "/api/v1/benchmarks",
        json={
            "scenarios": ["clean"],
            "anchor_counts": [8],
            "seeds": [101],
            "steps": 10,
        },
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/benchmarks/{job_id}").json()
        if job["status"] in ("complete", "failed"):
            break
        time.sleep(0.02)
    assert job["status"] == "complete"
    assert job["progress"] == 1
    assert len(job["result"]["rows"]) == 2
    assert job["result"]["rows"][0]["rmse"] < 1e-5


def test_layout_sites_endpoint_reports_legality():
    response = client.get("/api/v1/layouts/sites", params={"environment_id": "hospital_floor_01"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["floor_id"] == "hospital_floor_01"
    assert payload["source"] == "derived"
    assert payload["policy"]["min_anchor_separation"] == 4.0
    legal = [s for s in payload["sites"] if s["legal"]]
    assert len(legal) >= 8
    assert all("reason" in s for s in payload["sites"])


def test_layout_sites_unknown_environment_rejected():
    assert (
        client.get("/api/v1/layouts/sites", params={"environment_id": "missing"}).status_code == 404
    )


def test_layout_optimize_job_lifecycle():
    import time

    response = client.post(
        "/api/v1/layouts/optimize",
        json={
            "environment_id": "hospital_floor_01",
            "budgets": [3],
            "with_locus_estimator": False,
            "development_steps": 4,
            "validation_steps": 4,
            "held_out_steps": 4,
        },
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    # Single-flight guard: a second job while running is refused.
    assert (
        client.post(
            "/api/v1/layouts/optimize",
            json={"environment_id": "hospital_floor_01", "budgets": [4]},
        ).status_code
        in (202, 409)
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/layouts/optimize/{job_id}").json()
        if job["status"] in ("complete", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "complete", job.get("error")
    assert job["progress"] == 1.0
    result = job["result"]
    assert result["optimizer_protocol"]
    assert "3" in result["optimization"]
    selected = result["optimization"]["3"]["selected"]
    assert selected["budget"] == 3
    assert len(selected["anchors"]) == 3
    # Comparison reports manual/grid/optimized rows at equal budget on held-out.
    rows = result["comparisons"]["3/baseline"]["rows"]
    pooled = [r for r in rows if r["scenario"] == "pooled"]
    assert {r["layout"] for r in pooled} == {"manual", "grid", "optimized"}
    assert all(r["budget"] == 3 for r in pooled)


def test_layout_optimize_validation_errors():
    assert (
        client.post(
            "/api/v1/layouts/optimize", json={"environment_id": "missing", "budgets": [3]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/layouts/optimize", json={"environment_id": "hospital_floor_01", "budgets": [0]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/layouts/optimize",
            json={"environment_id": "hospital_floor_01", "budgets": [3, 3]},
        ).status_code
        == 422
    )
    assert client.get("/api/v1/layouts/optimize/missing").status_code == 404


def test_environment_registration_round_trip():
    base = client.get("/api/v1/environments").json()["environments"][0]
    duplicate = client.post("/api/v1/environments", json={"environment": base})
    assert duplicate.status_code == 409
    invalid = client.post("/api/v1/environments", json={"environment": {"id": "x"}})
    assert invalid.status_code == 422
    # A valid but new environment registers and is immediately usable.
    variant = dict(base)
    variant["id"] = "hospital_floor_01_probe"
    variant["name"] = "Probe floor"
    created = client.post("/api/v1/environments", json={"environment": variant})
    assert created.status_code == 201
    assert created.json()["id"] == "hospital_floor_01_probe"
    sites = client.get(
        "/api/v1/layouts/sites", params={"environment_id": "hospital_floor_01_probe"}
    )
    assert sites.status_code == 200
