"""API contract for multi-floor sessions and the additive legacy payload.

The two properties that matter here are:

* a legacy client sees the same keys with the same meanings; the only additions are
  null-valued, so a client that ignores unknown fields is unaffected;
* a multi-floor frame is honest: no 2D result is fabricated, an ambiguous storey is
  reported as ambiguous, and a client cannot choose or through the floor endpoint.
"""

import math

import pytest
from fastapi.testclient import TestClient

from backend.locus_x.main import app, environments, sessions
from backend.locus_x.schemas import Frame

client = TestClient(app)

BUILDING = "hospital_v3"
ROUTE = "ascent_stairs"
OTHER_ROUTE = "ascent_lift"

MULTIFLOOR_ONLY = {
    "floor_state",
    "transition",
    "elevation_estimate",
    "floor_probabilities",
}
"""Frame fields added for multi-floor frames; all null on a legacy frame."""

AI_KEYS = {
    "ai",
    "ai_tracking",
    "ai_metadata",
}
"""Frame fields added for AI localization."""

LEGACY_KEYS = {
    "session_id",
    "environment_id",
    "environment_version",
    "environment_sha256",
    "floor_id",
    "floor_elevation",
    "localization_mode",
    "revision",
    "frame_index",
    "timestamp",
    "settings",
    "anchors",
    "measurements",
    "simulator_labels",
    "baseline",
    "robust",
    "tracking",
    "evaluation",
    "events",
}
"""Every top-level key a pre-multi-floor client already consumed."""


@pytest.fixture(autouse=True)
def clear_sessions():
    sessions.clear()
    snapshot = dict(environments)
    yield
    sessions.clear()
    environments.clear()
    environments.update(snapshot)


def create(**overrides):
    payload = {
        "environment_id": BUILDING,
        "multifloor": {"mode": "inferred", "route_id": ROUTE},
        **overrides,
    }
    response = client.post("/api/v1/sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def path(frame):
    return f"/api/v1/sessions/{frame['session_id']}"


def test_create_is_honest_about_2d_and_truth():
    frame = create()
    assert frame["localization_mode"] == "multifloor_inferred"
    assert frame["floor_state"]["source"] == "inferred"
    # 2D baseline is not run, not faked with a zero.
    assert frame["baseline"]["status"] == "not_applicable"
    assert frame["baseline"]["position"] is None
    assert frame["baseline"]["reason"]
    # A 3D solve reports its horizontal projection and 3x3 covariance.
    assert frame["robust"]["position"] is not None
    assert len(frame["robust"]["covariance"]) == 3
    # Truth exists only in the evaluation/simulator blocks, at its real elevation.
    ground_truth = frame["evaluation"]["ground_truth"]
    assert len(ground_truth) == 2
    assert frame["evaluation"]["ground_truth_elevation"] is not None
    assert frame["elevation_estimate"] is not None
    assert all(target not in frame["measurements"][0] for target in ("true_range", "ground_truth"))
    # Probabilities are finite and cover the building, never NaN/inf.
    assert frame["floor_probabilities"]
    assert all(
        floor in frame["floor_probabilities"] and math.isfinite(value)
        for floor, value in frame["floor_probabilities"].items()
    )


def test_advance_controls_reset_and_delete():
    frame = create()
    session = path(frame)
    for _ in range(3):
        frame = client.post(
            f"{session}/advance", json={"expected_revision": frame["revision"], "dt": 0.5}
        ).json()
    assert frame["revision"] == 3
    assert frame["timestamp"] == 1.5
    assert frame["frame_index"] == 3

    # A fault-only edit recomputes the instant without restarting.
    edited = client.patch(
        f"{session}/controls",
        json={"expected_revision": frame["revision"], "scenario": "chaos"},
    ).json()
    assert edited["timestamp"] == frame["timestamp"]
    assert edited["frame_index"] == frame["frame_index"]
    assert edited["settings"]["noise_std"] == 0.65
    assert edited["revision"] == frame["revision"] + 1
    assert edited["baseline"]["status"] == "not_applicable"

    # A route edit restarts the multi-floor run at t=0 on the new route.
    restarted = client.patch(
        f"{session}/controls",
        json={"expected_revision": edited["revision"], "route": OTHER_ROUTE},
    ).json()
    assert restarted["timestamp"] == 0
    assert restarted["frame_index"] == 0
    assert restarted["settings"]["route"] == OTHER_ROUTE

    # Speed and seed also restart, because they define the truth trajectory.
    faster = client.patch(
        f"{session}/controls", json={"expected_revision": restarted["revision"], "speed": 2}
    ).json()
    assert faster["timestamp"] == 0 and faster["settings"]["speed"] == 2
    reseeded = client.patch(
        f"{session}/controls", json={"expected_revision": faster["revision"], "seed": 7}
    ).json()
    assert reseeded["timestamp"] == 0 and reseeded["settings"]["seed"] == 7

    # A per-floor anchor disable is honoured in the simulator labels.
    disabled = client.patch(
        f"{session}/controls",
        json={
            "expected_revision": reseeded["revision"],
            "disabled_anchor_keys": ["ground:A1"],
        },
    ).json()
    kinds = [label["kind"] for label in disabled["simulator_labels"]]
    assert kinds.count("disabled") == 1
    assert disabled["settings"]["disabled_anchor_keys"] == ["ground:A1"]
    assert disabled["timestamp"] == 0

    # Reset restores the route, mode, settings and time captured at creation.
    reset = client.post(f"{session}/reset", json={"expected_revision": disabled["revision"]}).json()
    assert reset["timestamp"] == 0
    assert reset["settings"]["route"] == ROUTE
    assert reset["settings"]["seed"] == 12
    assert reset["settings"]["disabled_anchor_keys"] == []
    assert reset["localization_mode"] == "multifloor_inferred"

    assert client.delete(session).status_code == 204
    assert client.get(session).status_code == 404


def test_stale_revision_and_invalid_requests_are_refused():
    frame = create()
    session = path(frame)
    assert (
        client.post(f"{session}/advance", json={"expected_revision": 99, "dt": 0.5}).status_code
        == 409
    )
    assert (
        client.patch(f"{session}/controls", json={"expected_revision": 99, "speed": 2}).status_code
        == 409
    )
    assert client.post(f"{session}/reset", json={"expected_revision": 99}).status_code == 409
    # The revision never moved on a refusal.
    assert client.get(session).json()["revision"] == frame["revision"]

    bad = [
        {"multifloor": {"mode": "teleport", "route_id": ROUTE}},
        {"multifloor": {"mode": "inferred", "route_id": "no_such_route"}},
        {"multifloor": {"mode": "inferred", "route_id": ROUTE, "device_height": 9}},
        {"multifloor": {"mode": "inferred", "route_id": ROUTE}, "floor_id": "first"},
        {
            "environment_id": "hospital_floor_01",
            "multifloor": {"mode": "inferred", "route_id": ROUTE},
        },
        {"environment_id": BUILDING, "multifloor": {"mode": "inferred"}},
    ]
    for payload in bad:
        assert client.post("/api/v1/sessions", json=payload).status_code == 422, payload

    # A floor cannot be selected for a derived-storey session.
    refused = client.post(
        f"{session}/floor",
        json={"expected_revision": frame["revision"], "floor_id": "first"},
    )
    assert refused.status_code == 409
    assert "derived, not selected" in refused.json()["detail"]


def test_route_assisted_source_is_configuration_not_inference():
    frame = create(multifloor={"mode": "route_assisted", "route_id": ROUTE})
    assert frame["localization_mode"] == "multifloor_route_assisted"
    assert frame["floor_state"]["source"] == "route_assisted"
    # Route assistance may name a floor but never manufactures a confidence.
    assert frame["floor_state"]["confidence"] is None
    # Hypothesis probabilities are still computed over every floor, not narrowed.
    assert set(frame["floor_probabilities"]) == {"ground", "first", "second"}


def test_inferred_transition_carries_no_route_witness():
    frame = create()
    session = path(frame)
    saw_transition = False
    for _ in range(60):
        frame = client.post(
            f"{session}/advance",
            json={"expected_revision": frame["revision"], "dt": 0.5},
        ).json()
        transition = frame["transition"]
        if transition is not None:
            saw_transition = True
            assert transition["phase"] in {"entering", "traversing", "exiting", "reacquiring"}
            # Inferred mode never receives route truth, so it cannot name the traverse.
            assert transition["transition_id"] is None
            assert transition["connection_id"] is None
            assert transition["from_floor_id"] is None
            assert transition["to_floor_id"] is None
            assert transition["progress"] is None
    assert saw_transition
    # An ambiguous storey claims nothing: null floor, no confidence, no elevation.
    if frame["frame_index"] == 0 or frame["floor_state"]["ambiguous"]:
        assert frame["floor_state"]["floor_id"] is None
        assert frame["floor_state"]["confidence"] is None
        assert frame["floor_id"] is None
        assert frame["floor_elevation"] is None


def test_legacy_payload_keeps_its_keys_and_values():
    response = client.post("/api/v1/sessions", json={})
    assert response.status_code == 201
    frame = response.json()
    assert set(frame) == LEGACY_KEYS | MULTIFLOOR_ONLY | AI_KEYS
    for key in LEGACY_KEYS:
        assert key in frame
    # The multi-floor fields are additive and null; no legacy value changes meaning.
    assert frame["floor_state"] is None
    assert frame["transition"] is None
    assert frame["elevation_estimate"] is None
    assert frame["floor_probabilities"] is None
    assert frame["evaluation"]["ground_truth_elevation"] is None
    assert frame["localization_mode"] == "2d_on_known_floor"
    assert frame["floor_id"] == "hospital_floor_01"
    assert frame["floor_elevation"] == 0.0
    # The response model accepts the additive fields, so a legacy client models it too.
    assert Frame.model_validate(frame).model_dump(mode="json") == frame


def test_legacy_floor_selection_and_environment_hash_are_untouched():
    frame = client.post(
        "/api/v1/sessions", json={"environment_id": BUILDING, "floor_id": "ground"}
    ).json()
    session = path(frame)
    selected = client.post(
        f"{session}/floor",
        json={"expected_revision": frame["revision"], "floor_id": "second"},
    )
    assert selected.status_code == 200
    assert selected.json()["floor_id"] == "second"
    assert selected.json()["frame_index"] == 0

    # The pinned hash is of the validated model, and the config file itself is untouched.
    legacy = client.post("/api/v1/sessions", json={}).json()
    assert (
        legacy["environment_sha256"]
        == "fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39"
    )
