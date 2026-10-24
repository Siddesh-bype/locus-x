import json

import numpy as np
import pytest

from backend.locus_x import ai_phase3


def metric(name, difference=-0.01, ci_high=-0.001, scope="focus", label="improved"):
    return {
        "variant": "all_mapfree",
        "scope": scope,
        "name": name,
        "difference": difference,
        "ci_high": ci_high,
        "label": label,
    }


def focus_rows():
    return [
        metric("solvable_majority_nlos"),
        metric("solvable_all_nlos"),
        metric("nlos_packet"),
        metric("nlos_packet_outage"),
    ]


def test_policy_uses_nlos_probabilities_and_ambiguous_fallback():
    weights, ambiguous, predictions = ai_phase3._policy(
        np.asarray([0.99, 0.99, 0.99, 0.0]), ["A1", "A2", "A3", "A4"]
    )
    assert weights is None
    assert ambiguous is True
    assert [prediction.p_los for prediction in predictions] == pytest.approx(
        [0.01, 0.01, 0.01, 1.0]
    )

    weights, ambiguous, _ = ai_phase3._policy(
        np.asarray([0.0, 0.1, 0.2, 0.9]), ["A1", "A2", "A3", "A4"]
    )
    assert ambiguous is False
    assert weights == {"A1": 1.0, "A2": 1.0, "A3": 1.0, "A4": 1.0}


def test_checkpoint_probabilities_use_local_feature_subset():
    checkpoint = {
        "mean": np.asarray([0.5, 1.0]),
        "scale": np.asarray([2.0, 4.0]),
        "W1": np.zeros((2, 32)),
        "b1": np.zeros(32),
        "W2": np.zeros((32, 16)),
        "b2": np.zeros(16),
        "W3": np.zeros((16, 1)),
        "b3": np.ones(1),
        "calibration": np.asarray([2.0, -1.0]),
    }
    probabilities = ai_phase3._checkpoint_probabilities(
        checkpoint, np.asarray([[1.0, 0.0, 3.0], [2.0, 0.0, 5.0]]), [0, 2]
    )
    assert probabilities == pytest.approx([1.0 / (1.0 + np.exp(-1.0))] * 2)


def test_end_to_end_model_names_include_controls_and_selected_subset():
    names = ai_phase3._end_to_end_model_names()
    assert names[0] == "v1"
    assert names[-1] == "all_map"
    assert "all_mapfree" in names
    selected = ai_phase3._end_to_end_model_names(selected_only=True)
    assert selected == ["v1", "g0_full", "g0_mapfree", "all_mapfree", "all_map"]


def test_validation_variants_include_anchor_failure_control():
    config = ai_phase3.load_config()
    ids = [variant["id"] for variant in ai_phase3._variants(config["splits"]["validation"])]
    assert "anchor_failure" in ids
    assert "nlos_packet_outage" in ids
    assert "all_four" in ids


def test_selection_retains_v1_without_supported_focus_improvement(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_phase3, "OUTPUT", tmp_path)
    rows = [
        metric("solvable_majority_nlos", difference=0.01, ci_high=0.02),
        metric("solvable_all_nlos", difference=0.01, ci_high=0.02),
    ]
    selection = ai_phase3._write_selection({}, rows)
    assert selection["selected"] == "v1"
    assert json.loads((tmp_path / "selection.json").read_text())["selected"] == "v1"


def test_anchor_count_regression_disqualifies_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_phase3, "OUTPUT", tmp_path)
    rows = [
        *focus_rows(),
        metric(
            "6",
            difference=0.02,
            ci_high=0.01,
            scope="anchor_count",
            label="regressed",
        ),
    ]
    selection = ai_phase3._write_selection({}, rows)
    assert selection["selected"] == "v1"
    assert selection["candidates"][next(iter(selection["candidates"]))][
        "disqualified_regressions"
    ] == 1


def test_supported_focus_selects_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_phase3, "OUTPUT", tmp_path)
    selection = ai_phase3._write_selection({}, focus_rows())
    assert selection["selected"] != "v1"
    assert selection["eligible"]
