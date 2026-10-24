from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.locus_x.diagnostics import recall_diagnosis as diagnosis

ROOT = Path(__file__).resolve().parents[2]


def test_seed_manifest_rejects_fresh_seed_seen_in_saved_data(monkeypatch):
    monkeypatch.setattr(
        diagnosis,
        "load_final_data",
        lambda: ({"seed": np.array([12001], dtype=int)}, {}),
    )
    monkeypatch.setattr(
        diagnosis,
        "load_validation_data",
        lambda: ({"seed": np.array([7001], dtype=int)}, {}, {}, {}),
    )
    with pytest.raises(ValueError, match="already in final_test"):
        diagnosis.verify_seed_manifest()


def test_seed_manifest_accepts_only_unused_seeds(monkeypatch):
    monkeypatch.setattr(
        diagnosis,
        "load_final_data",
        lambda: ({"seed": np.array([8001], dtype=int)}, {}),
    )
    monkeypatch.setattr(
        diagnosis,
        "load_validation_data",
        lambda: ({"seed": np.array([7001], dtype=int)}, {}, {}, {}),
    )
    result = diagnosis.verify_seed_manifest()
    assert len(result["fresh_seeds"]) >= 8
    assert set(result["fresh_seeds"]).isdisjoint({8001, 7001})
    assert result["status"] == "reserved and not evaluated"


def test_classification_counts_and_rates():
    result = diagnosis._classification(np.array([0, 1, 1, 0]), np.array([0.2, 0.8, 0.4, 0.6]), 0.5)
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (1, 1, 1, 1)
    assert result["precision"] == pytest.approx(0.5)
    assert result["recall"] == pytest.approx(0.5)
    assert result["f1"] == pytest.approx(0.5)
    assert result["specificity"] == pytest.approx(0.5)


def test_run_grouped_bootstrap_is_finite_and_signed():
    result = diagnosis._bootstrap_difference(
        np.array([1.0, 1.0, 1.0, 1.0]),
        np.array([0.5, 0.5, 0.5, 0.5]),
        np.array([1, 2, 3, 4]),
        resamples=100,
    )
    assert result["method_rmse"] < result["stat_rmse"]
    assert result["difference"] < 0
    assert result["relative_percent"] < 0
    assert result["ci_low"] <= result["ci_high"]
    assert np.isfinite([result["ci_low"], result["ci_high"]]).all()


def test_sample_to_frame_mapping_and_mismatch_detection():
    frames = {
        "run_id": np.array([1, 1, 1]),
        "step": np.array([0, 1, 2]),
    }
    samples = {
        "run_id": np.array([1, 1]),
        "step": np.array([0, 2]),
    }
    assert np.array_equal(diagnosis._frame_positions(samples, frames), [0, 2])
    broken = {"run_id": np.array([1]), "step": np.array([3])}
    with pytest.raises(RuntimeError, match="mapping"):
        diagnosis._frame_positions(broken, frames)


def test_degraded_matrix_marks_truth_and_preserves_unavailable_anchors():
    truth = np.array([[0.0, 1.0], [np.nan, 0.0]])
    degraded = diagnosis._degraded_matrix(truth, 1.0, false_alarm_rate=0.0, seed=7)
    assert np.isnan(degraded[1, 0])
    assert degraded[0, 0] == 0.0
    assert degraded[0, 1] == 1.0
    assert set(np.unique(degraded[np.isfinite(degraded)])).issubset({0.0, 1.0})


def test_inference_modules_do_not_import_post_hoc_diagnostics():
    for relative in (
        "backend/locus_x/reliability.py",
        "backend/locus_x/reliability_v2.py",
        "backend/locus_x/policy_v2.py",
        "backend/locus_x/ai_final.py",
        "backend/locus_x/estimators.py",
        "backend/locus_x/tracking.py",
        "backend/locus_x/__init__.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "recall_diagnosis" not in text
        assert "locus_x.diagnostics" not in text
