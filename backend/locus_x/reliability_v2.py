"""Causal v2 LOS/NLOS features and a self-contained model adapter.

This module is inference-safe. It reads only the public measurement surface,
known anchor geometry, a preliminary position estimate, causal history, and the
statistical tracker state. It does not import the simulator or evaluation layer,
and it never accepts truth coordinates, fault labels, injected bias, or route
state.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import numpy as np

from .estimators import valid_observations
from .reliability import (
    FEATURE_NAMES,
    FeatureBatch,
    FeatureHistory,
    ReliabilityOutput,
    extract_features,
)
from .schemas import AIMetadata, AIPrediction, Environment, Estimate, Measurement

FEATURE_VERSION_V2 = "2.0.0"
MODEL_VERSION_V2 = "ai-reliability-v2"
DEFAULT_MODEL_DIR_V2 = Path(__file__).resolve().parents[2] / "models" / MODEL_VERSION_V2

FRAME_GAP_CAP = 8
RANGE_WINDOW = 5
RECEPTION_WINDOW = 8
TRACKER_MAX_AGE_S = 3.0
MAX_CART_SPEED_MPS = 3.0

G2_RANGE = [
    "range_window_mean_norm",
    "range_window_variance_norm",
    "range_last_diff_norm",
    "range_rate_norm",
    "implied_speed_ratio",
]
G3_CONSISTENCY = [
    "pair_violation_count_norm",
    "pair_violation_magnitude_norm",
]
G4_TRACKER = [
    "tracker_range_residual_norm",
    "tracker_prediction_available",
    "tracker_state_age_norm",
]
G5_RECEPTION = [
    "reception_rate",
    "reception_miss_streak_norm",
    "reception_last_frame",
    "available_anchor_count_norm",
]
G1_MISSINGNESS = [
    "has_previous_observation",
    "frames_since_last_observation_norm",
    "previous_residual_missing",
]

FEATURE_NAMES_V2 = [
    *FEATURE_NAMES,
    *G2_RANGE,
    *G3_CONSISTENCY,
    *G4_TRACKER,
    *G5_RECEPTION,
    *G1_MISSINGNESS,
]

V2_GROUPS = {
    "G2": G2_RANGE,
    "G3": G3_CONSISTENCY,
    "G4": G4_TRACKER,
    "G5": G5_RECEPTION,
    "G1": G1_MISSINGNESS,
}
FEATURE_GROUPS_V2 = {"G0": list(FEATURE_NAMES), **V2_GROUPS}
MAP_FEATURES = ("wall_count_scaled", "frame_wall_mean_scaled")
G0_MAPFREE_NAMES = [name for name in FEATURE_NAMES if name not in MAP_FEATURES]


@dataclass
class FeatureHistoryV2(FeatureHistory):
    """Causal v2 state. Inherits the unchanged v1 temporal fields."""

    frame_index: int = 0
    last_seen_frame: dict[str, int] = field(default_factory=dict)
    range_windows: dict[str, deque[tuple[float, float]]] = field(default_factory=dict)
    reception_windows: dict[str, deque[int]] = field(default_factory=dict)
    tracker_state: np.ndarray | None = None
    tracker_timestamp: float | None = None

    def clear(self):
        super().clear()
        self.frame_index = 0
        self.last_seen_frame.clear()
        self.range_windows.clear()
        self.reception_windows.clear()
        self.tracker_state = None
        self.tracker_timestamp = None


@dataclass
class FeatureBatchV2:
    anchors: list[str]
    matrix: np.ndarray
    base: FeatureBatch
    frame_index: int


def _range_window(history: FeatureHistoryV2, anchor_id: str) -> list[tuple[float, float]]:
    return list(history.range_windows.get(anchor_id, ()))[-(RANGE_WINDOW - 1) :]


def _reception_window(history: FeatureHistoryV2, anchor_id: str) -> list[int]:
    return list(history.reception_windows.get(anchor_id, ()))


def _snapshot(history: FeatureHistoryV2):
    return (
        dict(history.residuals),
        dict(history.last_seen_frame),
        {key: list(value) for key, value in history.range_windows.items()},
        {key: list(value) for key, value in history.reception_windows.items()},
    )


def _advance_v2_history(
    history: FeatureHistoryV2,
    valid: list[Measurement],
    environment: Environment,
    frame_index: int,
) -> None:
    history.frame_index = frame_index + 1
    observed = {measurement.anchor_id for measurement in valid}
    for measurement in valid:
        anchor_id = measurement.anchor_id
        history.last_seen_frame[anchor_id] = frame_index
        window = history.range_windows.setdefault(
            anchor_id, deque(maxlen=RANGE_WINDOW)
        )
        window.append((float(measurement.timestamp), float(measurement.observed_range)))
    for anchor in environment.anchors:
        window = history.reception_windows.setdefault(
            anchor.id, deque(maxlen=RECEPTION_WINDOW)
        )
        window.append(1 if anchor.id in observed else 0)


def _trailing_zero_streak(values: list[int]) -> int:
    streak = 0
    for value in reversed(values):
        if value:
            break
        streak += 1
    return streak


def _feature_row(
    *,
    measurement: Measurement,
    base_row: np.ndarray,
    environment: Environment,
    history: FeatureHistoryV2,
    prior_residuals: dict[str, float],
    prior_last_seen: dict[str, int],
    prior_ranges: dict[str, list[tuple[float, float]]],
    prior_reception: dict[str, list[int]],
    frame_index: int,
    diagonal: float,
    available_count: int,
    tracker_state: np.ndarray | None,
    tracker_timestamp: float | None,
) -> list[float]:
    anchor_id = measurement.anchor_id
    anchor = np.asarray(measurement.anchor_position, dtype=float)
    observed = float(measurement.observed_range)
    timestamp = float(measurement.timestamp)

    prior_range = prior_ranges.get(anchor_id, [])[-(RANGE_WINDOW - 1) :]
    range_values = [value for _, value in prior_range] + [observed]
    range_mean = float(np.mean(range_values))
    range_variance = float(np.var(range_values))
    if prior_range:
        previous_time, previous_range = prior_range[-1]
        delta_time = max(0.0, min(2.0, timestamp - previous_time))
        last_diff = observed - previous_range
    else:
        delta_time = 0.0
        last_diff = 0.0
    if delta_time > 1e-9:
        range_rate = last_diff / delta_time
        implied_speed = abs(last_diff) / (delta_time * MAX_CART_SPEED_MPS)
    else:
        range_rate = 0.0
        implied_speed = 0.0

    last_seen = prior_last_seen.get(anchor_id)
    has_previous = 0.0 if last_seen is None else 1.0
    gap = FRAME_GAP_CAP if last_seen is None else frame_index - last_seen
    gap_norm = min(FRAME_GAP_CAP, max(0, gap)) / FRAME_GAP_CAP
    previous_residual_missing = 0.0 if anchor_id in prior_residuals else 1.0

    valid = valid_observations([measurement])
    reception = prior_reception.get(anchor_id, [])
    reception_window = reception[-(RECEPTION_WINDOW - 1) :] + ([1] if valid else [0])
    reception_rate = float(np.mean(reception_window))
    miss_streak = _trailing_zero_streak(reception)
    reception_last = float(reception[-1]) if reception else 0.0

    if (
        tracker_state is not None
        and tracker_timestamp is not None
        and np.asarray(tracker_state).shape == (4,)
        and np.isfinite(tracker_state).all()
    ):
        age = timestamp - float(tracker_timestamp)
        tracker_available = 1.0 if -1e-9 <= age <= TRACKER_MAX_AGE_S else 0.0
    else:
        age = TRACKER_MAX_AGE_S
        tracker_available = 0.0
    if tracker_available:
        predicted_position = np.asarray(tracker_state[:2]) + np.asarray(
            tracker_state[2:]
        ) * max(0.0, age)
        predicted_range = float(np.linalg.norm(anchor - predicted_position))
        tracker_residual = observed - predicted_range
        age_norm = min(TRACKER_MAX_AGE_S, max(0.0, age)) / TRACKER_MAX_AGE_S
    else:
        tracker_residual = 0.0
        age_norm = 1.0

    return [
        *base_row.tolist(),
        range_mean / diagonal,
        range_variance / (diagonal**2),
        last_diff / diagonal,
        range_rate / MAX_CART_SPEED_MPS,
        implied_speed,
        np.nan,  # G3 is filled after all anchors in this frame are collected.
        np.nan,
        tracker_residual / diagonal,
        tracker_available,
        age_norm,
        reception_rate,
        min(RECEPTION_WINDOW, miss_streak) / RECEPTION_WINDOW,
        reception_last,
        available_count / max(1, len(environment.anchors)),
        has_previous,
        gap_norm,
        previous_residual_missing,
    ]


def extract_features_v2(
    measurements: list[Measurement],
    environment: Environment,
    estimate: Estimate | None,
    history: FeatureHistoryV2,
    tracker_state: np.ndarray | None = None,
    tracker_timestamp: float | None = None,
) -> FeatureBatchV2 | None:
    """Build causal v2 features and advance v2 history exactly once.

    G0 is produced by the unchanged v1 extractor. The remaining groups use the
    pre-call history snapshot, so they cannot see the current frame's residuals,
    ranges or reception pattern through their own state.
    """
    frame_index = history.frame_index
    prior_residuals, prior_last_seen, prior_ranges, prior_reception = _snapshot(history)
    valid = valid_observations(measurements)
    base = extract_features(measurements, environment, estimate, history)
    _advance_v2_history(history, valid, environment, frame_index)
    if base is None:
        return None
    if tracker_state is None:
        tracker_state = history.tracker_state
    if tracker_timestamp is None:
        tracker_timestamp = history.tracker_timestamp
    diagonal = max(1e-6, float(np.hypot(environment.width, environment.height)))
    rows = [
        _feature_row(
            measurement=measurement,
            base_row=base.matrix[index],
            environment=environment,
            history=history,
            prior_residuals=prior_residuals,
            prior_last_seen=prior_last_seen,
            prior_ranges=prior_ranges,
            prior_reception=prior_reception,
            frame_index=frame_index,
            diagonal=diagonal,
            available_count=len(valid),
            tracker_state=tracker_state,
            tracker_timestamp=tracker_timestamp,
        )
        for index, measurement in enumerate(valid)
    ]
    matrix = np.asarray(rows, dtype=float)
    positions = np.asarray([measurement.anchor_position for measurement in valid], dtype=float)
    observed = np.asarray([measurement.observed_range for measurement in valid], dtype=float)
    counts = np.zeros(len(valid))
    magnitudes = np.zeros(len(valid))
    for left in range(len(valid)):
        for right in range(left + 1, len(valid)):
            distance = float(np.linalg.norm(positions[left] - positions[right]))
            lower = max(0.0, abs(observed[left] - observed[right]) - distance)
            upper = max(0.0, distance - observed[left] - observed[right])
            violation = lower + upper
            if violation > 0:
                counts[left] += 1
                counts[right] += 1
                magnitudes[left] += violation
                magnitudes[right] += violation
    count_column = FEATURE_NAMES_V2.index(G3_CONSISTENCY[0])
    magnitude_column = FEATURE_NAMES_V2.index(G3_CONSISTENCY[1])
    matrix[:, count_column] = counts / max(1, len(valid) - 1)
    matrix[:, magnitude_column] = np.minimum(2.0, magnitudes / diagonal)
    return FeatureBatchV2(
        anchors=base.anchors,
        matrix=matrix,
        base=base,
        frame_index=frame_index,
    )


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60, 60)))


def _forward(weights: dict[str, np.ndarray], matrix: np.ndarray) -> np.ndarray:
    hidden = np.maximum(0.0, matrix @ weights["W1"] + weights["b1"])
    hidden = np.maximum(0.0, hidden @ weights["W2"] + weights["b2"])
    return (hidden @ weights["W3"] + weights["b3"]).reshape(-1)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReliabilityV2Model:
    """NumPy v2 ensemble adapter with a deterministic statistical fallback."""

    def __init__(
        self,
        checkpoints: list[dict],
        *,
        available: bool,
        error: str | None = None,
        model_version: str = MODEL_VERSION_V2,
    ):
        self.checkpoints = checkpoints
        self.available = bool(available and checkpoints)
        self.error = error
        self.model_version = model_version
        self.feature_version = FEATURE_VERSION_V2
        self.feature_names = list(FEATURE_NAMES_V2)

    @classmethod
    def load(cls, directory: Path | str | None = None) -> ReliabilityV2Model:
        path = Path(directory) if directory is not None else DEFAULT_MODEL_DIR_V2
        try:
            config = json.loads((path / "config.json").read_text(encoding="utf-8"))
            if list(config.get("feature_names", [])) != FEATURE_NAMES_V2:
                raise ValueError("v2 feature schema mismatch")
            if config.get("feature_version") != FEATURE_VERSION_V2:
                raise ValueError("unsupported v2 feature version")
            checkpoints = []
            for entry in config.get("checkpoints", []):
                file_path = path / str(entry["file"])
                if not file_path.is_file():
                    raise ValueError(f"missing checkpoint {file_path.name}")
                if _sha256(file_path) != entry["sha256"]:
                    raise ValueError(f"checksum mismatch for {file_path.name}")
                with np.load(file_path, allow_pickle=False) as arrays:
                    checkpoint = {
                        "mean": np.asarray(arrays["mean"], dtype=float),
                        "scale": np.asarray(arrays["scale"], dtype=float),
                        "calibration": np.asarray(arrays["calibration"], dtype=float),
                        "weights": {
                            name: np.asarray(arrays[name], dtype=float)
                            for name in ("W1", "b1", "W2", "b2", "W3", "b3")
                        },
                    }
                if checkpoint["mean"].shape != (len(FEATURE_NAMES_V2),):
                    raise ValueError("bad checkpoint mean shape")
                if checkpoint["scale"].shape != (len(FEATURE_NAMES_V2),):
                    raise ValueError("bad checkpoint scale shape")
                if checkpoint["calibration"].shape != (2,):
                    raise ValueError("bad calibration shape")
                if not all(
                    np.isfinite(value).all()
                    for value in (
                        checkpoint["mean"],
                        checkpoint["scale"],
                        checkpoint["calibration"],
                        *checkpoint["weights"].values(),
                    )
                ):
                    raise ValueError("non-finite checkpoint values")
                if np.any(checkpoint["scale"] <= 0):
                    raise ValueError("non-positive feature scale")
                checkpoints.append(checkpoint)
            if not checkpoints:
                raise ValueError("no checkpoints")
            return cls(checkpoints, available=True)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return cls([], available=False, error=str(exc))

    def _fallback(
        self,
        start: float,
        reason: str,
    ) -> ReliabilityOutput:
        return ReliabilityOutput(
            weights=None,
            metadata=AIMetadata(
                status="fallback",
                reason=reason,
                model_version=self.model_version,
                feature_version=self.feature_version,
                ambiguous=False,
                n_predicted_los=0,
                n_predicted_nlos=0,
                predictions=[],
                runtime_ms=(perf_counter() - start) * 1000,
            ),
        )

    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        values = []
        for checkpoint in self.checkpoints:
            normalized = (matrix - checkpoint["mean"]) / checkpoint["scale"]
            logits = _forward(checkpoint["weights"], normalized)
            slope, intercept = checkpoint["calibration"]
            values.append(_sigmoid(slope * logits + intercept))
        return np.mean(np.asarray(values), axis=0)

    def predict(
        self,
        measurements: list[Measurement],
        environment: Environment,
        estimate: Estimate | None,
        history: FeatureHistoryV2,
        tracker_state: np.ndarray | None = None,
        tracker_timestamp: float | None = None,
    ) -> ReliabilityOutput:
        start = perf_counter()
        if not self.available:
            return self._fallback(
                start,
                f"v2 model unavailable: {self.error or 'not loaded'}",
            )
        batch = extract_features_v2(
            measurements,
            environment,
            estimate,
            history,
            tracker_state,
            tracker_timestamp,
        )
        if batch is None:
            return self._fallback(start, "v2 features unavailable; statistical fallback")
        if not np.isfinite(batch.matrix).all():
            return self._fallback(start, "non-finite v2 feature; statistical fallback")
        probabilities = self._probabilities(batch.matrix)
        if not np.isfinite(probabilities).all():
            return self._fallback(start, "non-finite v2 probability; statistical fallback")
        predictions = []
        for anchor, nlos_probability in zip(batch.anchors, probabilities, strict=True):
            p_los = float(np.clip(1.0 - nlos_probability, 0.0, 1.0))
            if p_los >= 0.05:
                weight = 1.0
            else:
                weight = 0.05 + 0.95 * (p_los / 0.05)
            predictions.append(AIPrediction(anchor_id=anchor, p_los=p_los, weight=float(weight)))
        predicted_los = sum(prediction.p_los >= 0.5 for prediction in predictions)
        ambiguous = predicted_los < 3
        return ReliabilityOutput(
            weights=None
            if ambiguous
            else {prediction.anchor_id: prediction.weight for prediction in predictions},
            metadata=AIMetadata(
                status="fallback" if ambiguous else "active",
                reason=(
                    "Fewer than three anchors predicted LOS; ambiguous consensus, "
                    "statistical fallback"
                    if ambiguous
                    else "v2 reliability active"
                ),
                model_version=self.model_version,
                feature_version=self.feature_version,
                ambiguous=ambiguous,
                n_predicted_los=predicted_los,
                n_predicted_nlos=len(predictions) - predicted_los,
                predictions=predictions,
                runtime_ms=(perf_counter() - start) * 1000,
            ),
        )


def load_default_model_v2() -> ReliabilityV2Model:
    return ReliabilityV2Model.load(DEFAULT_MODEL_DIR_V2)
