"""Test live telemetry and Wi-Fi ranging endpoint in simple room."""

from fastapi.testclient import TestClient
from backend.locus_x.main import app, sessions

client = TestClient(app)


def test_simple_room_session_and_live_telemetry():
    sessions.clear()
    # 1. Create a session in simple_room
    create_resp = client.post(
        "/api/v1/sessions",
        json={"environment_id": "simple_room"},
    )
    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]

    # 2. Push live range measurements (target at approx [5.0, 4.0])
    # Distances to APs: AP1(1,1)->5.0m, AP2(9,1)->5.0m, AP3(9,7)->5.0m, AP4(1,7)->5.0m
    live_resp = client.post(
        f"/api/v1/sessions/{session_id}/live",
        json={
            "ranges": {
                "AP1": 5.0,
                "AP2": 5.0,
                "AP3": 5.0,
                "AP4": 5.0,
            },
            "dt": 0.2,
        },
    )
    assert live_resp.status_code == 200, live_resp.text
    frame = live_resp.json()

    # Verify that all estimators processed the live ranges
    assert frame["frame_index"] == 1
    assert len(frame["measurements"]) == 4

    # Baseline estimator fix
    assert frame["baseline"]["status"] == "valid"
    assert frame["baseline"]["position"] is not None
    bx, by = frame["baseline"]["position"]
    assert 4.5 <= bx <= 5.5
    assert 3.5 <= by <= 4.5

    # Robust LOCUS-X estimator fix
    assert frame["robust"]["status"] == "valid"
    assert frame["robust"]["position"] is not None
    rx, ry = frame["robust"]["position"]
    assert 4.5 <= rx <= 5.5
    assert 3.5 <= ry <= 4.5

    # LOCUS-X + AI fix
    assert frame["ai"]["status"] == "valid"
    assert frame["ai"]["position"] is not None

    # Kalman tracking state
    assert frame["tracking"]["status"] in ("accepted", "degraded")
    sessions.clear()
