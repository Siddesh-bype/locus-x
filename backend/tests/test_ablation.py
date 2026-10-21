"""Unit invariants for the Phase 2 ablation harness."""

import subprocess
import sys

import numpy as np
import pytest

from backend.locus_x.ai_ablation import (
    CALIBRATION_FRACTION,
    FEATURE_NAMES,
    VARIANTS,
    feature_indices,
    feature_mask,
    grouped_calibration_split,
    variant_indices,
)


def test_feature_mask_selects_exact_named_columns_without_changing_rows():
    matrix = np.arange(24 * 5, dtype=np.float64).reshape(5, 24)
    indices = feature_indices(groups=["residual"])
    masked = feature_mask(matrix, indices)
    assert masked.shape == (5, len(indices))
    assert len(indices) > 0
    assert set(indices) == {
        FEATURE_NAMES.index(name) for name in (
            "observed_range_norm",
            "predicted_range_norm",
            "residual_m",
            "residual_norm",
            "abs_residual_norm",
            "instant_reliability",
            "consensus_median_norm",
            "consensus_mad_norm",
            "observed_to_predicted_delta",
            "residual_sign",
        )
    }
    np.testing.assert_array_equal(masked, matrix[:, indices])


def test_grouped_split_is_deterministic_and_never_splits_a_run():
    run_ids = np.repeat(np.arange(50), 4)
    mask = grouped_calibration_split(run_ids, fraction=CALIBRATION_FRACTION)
    np.testing.assert_array_equal(mask, grouped_calibration_split(run_ids))
    for run_id in np.unique(run_ids):
        run_mask = mask[run_ids == run_id]
        assert run_mask.all() or not run_mask.any()
    assert 0 < mask.sum() < len(mask)


def test_variant_feature_selection_does_not_change_split_assignment():
    rng = np.random.default_rng(7)
    matrix = rng.normal(size=(200, len(FEATURE_NAMES)))
    run_ids = np.repeat(np.arange(25), 8)
    baseline = grouped_calibration_split(run_ids)
    for variant in VARIANTS:
        indices = variant_indices(variant)
        if indices is not None:
            assert len(indices) > 0
            selected = feature_mask(matrix, indices)
            assert selected.shape == (len(matrix), len(indices))
        np.testing.assert_array_equal(grouped_calibration_split(run_ids), baseline)


def test_unknown_feature_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown features"):
        feature_indices(names=["not_a_feature"])


def test_diagnostic_module_is_not_imported_by_inference():
    code = (
        "import sys\n"
        "import backend.locus_x.main\n"
        "import backend.locus_x.session\n"
        "assert 'backend.locus_x.ai_ablation_diagnostics' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
