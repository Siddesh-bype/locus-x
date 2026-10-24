import pytest
from fastapi.testclient import TestClient

from backend.locus_x.main import app, sessions

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_sessions():
    sessions.clear()
    yield
    sessions.clear()


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


def test_ai_metadata_exposes_version_and_fallback():
    frame = client.post("/api/v1/sessions", json={"settings": {"scenario": "clean"}}).json()
    metadata = frame["ai_metadata"]
    assert metadata["model_version"] == "ai-reliability-v2"
    assert metadata["status"] in ("active", "fallback")
    path = f"/api/v1/sessions/{frame['session_id']}"
    frame = client.patch(
        f"{path}/controls", json={"expected_revision": frame["revision"], "dropout": 1}
    ).json()
    frame = client.post(
        f"{path}/advance", json={"expected_revision": frame["revision"], "dt": 1}
    ).json()
    assert frame["ai_metadata"]["status"] == "fallback"
    assert frame["ai_metadata"]["model_version"] == "ai-reliability-v2"


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
    assert len(job["result"]["rows"]) == 3
    assert {row["method"] for row in job["result"]["rows"]} == {
        "baseline",
        "proposed",
        "ai",
    }
    assert job["result"]["rows"][0]["rmse"] < 1e-5


def test_ai_metadata_reports_the_weights_actually_applied():
    frame = client.post("/api/v1/sessions", json={"settings": {"scenario": "nlos"}}).json()
    path = f"/api/v1/sessions/{frame['session_id']}"
    weights = []
    for _ in range(12):
        frame = client.post(
            f"{path}/advance", json={"expected_revision": frame["revision"], "dt": 0.5}
        ).json()
        weights += [p["weight"] for p in frame["ai_metadata"]["predictions"]]
    assert weights and min(weights) < 1.0
    assert all(0.0 <= w <= 1.0 for w in weights)
