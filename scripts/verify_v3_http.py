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
    return {"base_url": base_url, "floors": checks, "legacy_default": "passed"}


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
