"""Exercise the live local API and Vite proxy using disposable simulation sessions."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def verify(base_url):
    checks = []

    def call(path, body=None, method=None, expected=200):
        request = Request(
            base_url + "/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=method or ("POST" if body is not None else "GET"),
        )
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            assert error.code == expected, (path, error.code, error.read().decode())
            return None
        with response:
            assert response.status == expected, (path, response.status)
            content = response.read()
            return json.loads(content) if content else None

    assert call("/health")["version"] == "3.0.0"
    catalog = call("/environments")["environments"]
    building = next(env for env in catalog if env["id"] == "hospital_v3")
    assert len(building["floors"]) == 3
    for floor in building["floors"]:
        original = call(
            "/sessions",
            {
                "environment_id": building["id"],
                "floor_id": floor["id"],
                "settings": {"scenario": "clean", "seed": 29},
            },
            expected=201,
        )
        path = "/sessions/" + original["session_id"]
        try:
            assert original["floor_id"] == floor["id"]
            assert original["floor_elevation"] == floor["elevation"]
            assert original["anchors"] == floor["anchors"]
            assert original["evaluation"]["baseline_error"] < 1e-6
            advanced = call(path + "/advance", {"expected_revision": 0, "dt": 1})
            call(path + "/floor", {"expected_revision": 0, "floor_id": "ground"}, expected=409)
            call(path + "/floor", {"expected_revision": 1, "floor_id": "roof"}, expected=422)
            assert call(path) == advanced
            fault = call(
                path + "/controls",
                {
                    "expected_revision": advanced["revision"],
                    "scenario": "chaos",
                },
                method="PATCH",
            )
            assert fault["measurements"] != advanced["measurements"]
            target = "first" if floor["id"] != "first" else "second"
            changed = call(
                path + "/floor",
                {
                    "expected_revision": fault["revision"],
                    "floor_id": target,
                },
            )
            assert changed["floor_id"] == target and changed["timestamp"] == 0
            assert changed["settings"] == fault["settings"]
            assert changed["tracking"]["outage_seconds"] == 0
            reset = call(path + "/reset", {"expected_revision": changed["revision"]})
            assert reset["floor_id"] == original["floor_id"]
            assert reset["measurements"] == original["measurements"]
            assert reset["settings"] == original["settings"]
            checks.append(
                {"floor": floor["id"], "status": "passed", "elevation": floor["elevation"]}
            )
        finally:
            call(path, method="DELETE", expected=204)
    legacy = call("/sessions", {}, expected=201)
    assert legacy["floor_id"] == legacy["environment_id"] == "hospital_floor_01"
    call("/sessions/" + legacy["session_id"], method="DELETE", expected=204)

    multifloor = []
    for mode in ("inferred", "route_assisted"):
        floor_ids = [floor["id"] for floor in building["floors"]]
        session = call(
            "/sessions",
            {
                "environment_id": building["id"],
                "settings": {"scenario": "clean", "seed": 29},
                "multifloor": {"mode": mode, "route_id": "ascent_stairs"},
            },
            expected=201,
        )
        path = "/sessions/" + session["session_id"]
        try:
            assert session["localization_mode"] == f"multifloor_{mode}"
            assert session["floor_state"] is not None
            assert session["floor_state"]["source"] == (
                "route_assisted" if mode == "route_assisted" else "inferred"
            )
            # Likelihood scores over every storey, exported as finite numbers.
            assert sorted(session["floor_probabilities"]) == sorted(floor_ids)

            # A client may not choose the storey: the endpoint refuses before mutation.
            call(
                path + "/floor",
                {"expected_revision": session["revision"], "floor_id": "ground"},
                expected=409,
            )
            frame = session
            for _ in range(16):
                frame = call(path + "/advance", {"expected_revision": frame["revision"], "dt": 1})
            # 16 s is inside ascent_stairs' first traverse [15.46, 35.46) s.
            assert frame["timestamp"] == 16
            transition = frame["transition"]
            assert transition is not None
            if mode == "route_assisted":
                assert transition["phase"] == "traversing"
                assert transition["transition_id"] == "ascent-stairs-1"
                assert transition["connection_id"] == "west-stairs"
                assert transition["from_floor_id"] == "ground"
                assert transition["to_floor_id"] == "first"
                assert 0.0 < transition["progress"] < 1.0
                assert frame["floor_state"]["source"] == "route_assisted"
                assert frame["floor_state"]["ambiguous"] is True
            else:
                # Inferred mode gets no route witness, only the tracker's own phase.
                assert transition["phase"] in ("entering", "traversing", "exiting")
                assert transition["transition_id"] is None
                assert transition["connection_id"] is None
                assert frame["floor_state"]["source"] == "inferred"
                assert frame["floor_state"]["floor_id"] is None
            multifloor.append(
                {
                    "mode": mode,
                    "status": "passed",
                    "timestamp": frame["timestamp"],
                    "floor_state": frame["floor_state"],
                    "transition": transition,
                    "floor_endpoint": 409,
                }
            )
        finally:
            call(path, method="DELETE", expected=204)
    return {
        "base_url": base_url,
        "floors": checks,
        "legacy_default": "passed",
        "multifloor": multifloor,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.url.rstrip("/"))
    serialized = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
