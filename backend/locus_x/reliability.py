"""Inference-safe LOS/NLOS reliability model for range measurements.

The module deliberately has no imports from the simulator or evaluation layers. It
only sees the public ``Measurement`` surface, the known environment map, and a
position estimate produced from observations.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import numpy as np

from .estimators import valid_observations
from .geometry import intersects
from .schemas import AIMetadata, AIPrediction, Environment, Estimate, Measurement, Point

FEATURE_VERSION = "1.0.0"
MODEL_VERSION = "ai-reliability-v1"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / MODEL_VERSION

FEATURE_NAMES = [
    "observed_range_norm",
    "predicted_range_norm",
    "residual_m",
    "residual_norm",
    "abs_residual_norm",
    "instant_reliability",
    "anchor_x_norm",
    "anchor_y_norm",
    "relative_dx_norm",
    "relative_dy_norm",
    "bearing_sin",
    "bearing_cos",
    "available_fraction",
    "condition_log",
    "wall_count_scaled",
    "frame_wall_mean_scaled",
    "consensus_median_norm",
    "consensus_mad_norm",
    "observed_to_predicted_delta",
    "residual_sign",
    "previous_residual_norm",
    "residual_delta_norm",
    "position_delta_norm",
    "time_delta_s",
]

# These groups are used only for controlled ablations in the training CLI.
FEATURE_GROUPS = {
    "residual": [
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
    ],
    "geometry": [
        "anchor_x_norm",
        "anchor_y_norm",
        "relative_dx_norm",
        "relative_dy_norm",
        "bearing_sin",
        "bearing_cos",
        "available_fraction",
        "condition_log",
    ],
    "map": ["wall_count_scaled", "frame_wall_mean_scaled"],
    "temporal": [
        "previous_residual_norm",
        "residual_delta_norm",
        "position_delta_norm",
        "time_delta_s",
    ],
}


@dataclass
class FeatureHistory:
    """Temporal state built only from past observations and prior estimates."""

    residuals: dict[str, float] = field(default_factory=dict)
    position: Point | None = None
    timestamp: float | None = None

    def clear(self):
        self.residuals.clear()
        self.position = None
        self.timestamp = None


@dataclass
class FeatureBatch:
    anchors: list[str]
    matrix: np.ndarray
    normalized_residuals: dict[str, float]
    position: Point
    timestamp: float


@dataclass
class ReliabilityOutput:
    weights: dict[str, float] | None
    metadata: AIMetadata


def _mad(values: np.ndarray) -> float:
    median = float(np.median(values))
    return float(np.median(np.abs(values - median)))


def _wall_count(a: Point, b: Point, environment: Environment) -> int:
    return sum(intersects(a, b, wall.a, wall.b) for wall in environment.walls)


def extract_features(
    measurements: list[Measurement],
    environment: Environment,
    estimate: Estimate | None,
    history: FeatureHistory,
) -> FeatureBatch | None:
    """Build features that are available before ground truth is evaluated.

    ``history`` is read for temporal features and advanced once the batch has been
    computed. Nothing in this function accepts labels, truth coordinates, injected
    bias, or simulator fault flags.
    """
    valid = valid_observations(measurements)
    if len(valid) < 3 or estimate is None or estimate.position is None:
        return None
    position = tuple(float(v) for v in estimate.position)
    anchors = np.asarray([m.anchor_position for m in valid], dtype=float)
    observed = np.asarray([m.observed_range for m in valid], dtype=float)
    predicted = np.linalg.norm(anchors - np.asarray(position), axis=1)
    residual = observed - predicted
    scale = max(0.2, 1.4826 * _mad(residual))
    normalized = residual / scale
    instant = np.clip(1 / (1 + (np.abs(residual) / (2 * scale)) ** 2), 0.08, 1)
    diagonal = max(1e-6, float(np.hypot(environment.width, environment.height)))
    available_fraction = len(valid) / max(1, len(environment.anchors))
    condition = min(float(estimate.condition_number or 1e6), 1e6)
    median_normalized = float(np.median(normalized))
    mad_normalized = _mad(normalized)
    wall_counts = np.asarray(
        [_wall_count(position, m.anchor_position, environment) for m in valid], dtype=float
    )
    wall_mean = float(np.mean(wall_counts))
    previous_position = np.asarray(history.position) if history.position is not None else None
    position_delta = (
        float(np.linalg.norm(np.asarray(position) - previous_position) / diagonal)
        if previous_position is not None
        else 0.0
    )
    if history.timestamp is None:
        time_delta = 0.0
    else:
        current_time = float(valid[0].timestamp)
        time_delta = max(0.0, min(2.0, current_time - history.timestamp))
    rows = []
    normalized_by_anchor = {}
    for index, measurement in enumerate(valid):
        anchor = np.asarray(measurement.anchor_position, dtype=float)
        delta = anchor - np.asarray(position)
        distance = max(1e-9, float(np.linalg.norm(delta)))
        previous_residual = history.residuals.get(measurement.anchor_id, 0.0)
        norm_residual = float(normalized[index])
        normalized_by_anchor[measurement.anchor_id] = norm_residual
        rows.append(
            [
                observed[index] / diagonal,
                predicted[index] / diagonal,
                residual[index],
                norm_residual,
                abs(norm_residual),
                float(instant[index]),
                anchor[0] / max(1e-6, environment.width),
                anchor[1] / max(1e-6, environment.height),
                delta[0] / diagonal,
                delta[1] / diagonal,
                delta[1] / distance,
                delta[0] / distance,
                available_fraction,
                np.log1p(condition) / np.log(1e6),
                min(3.0, float(wall_counts[index])) / 3.0,
                min(3.0, wall_mean) / 3.0,
                median_normalized,
                mad_normalized,
                float(observed[index] / max(1e-9, predicted[index]) - 1.0),
                float(np.sign(residual[index])),
                previous_residual,
                norm_residual - previous_residual,
                position_delta,
                time_delta,
            ]
        )
    # Advance temporal state only after all rows for this frame have been built.
    history.residuals = normalized_by_anchor
    history.position = position
    history.timestamp = float(valid[0].timestamp)
    return FeatureBatch(
        anchors=[m.anchor_id for m in valid],
        matrix=np.asarray(rows, dtype=float),
        normalized_residuals=normalized_by_anchor,
        position=position,
        timestamp=float(valid[0].timestamp),
    )


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -60, 60)
    return 1.0 / (1.0 + np.exp(-clipped))


class ReliabilityModel:
    """Small NumPy MLP adapter with a deterministic statistical fallback."""

    def __init__(
        self,
        *,
        mean: np.ndarray,
        scale: np.ndarray,
        weights: dict[str, np.ndarray],
        feature_names: list[str],
        calibration: tuple[float, float],
        weight_floor: float,
        weight_threshold: float = 0.0,
        version: str = MODEL_VERSION,
        feature_version: str = FEATURE_VERSION,
    ):
        self.mean = mean
        self.scale = np.where(np.abs(scale) < 1e-9, 1.0, scale)
        self.weights = weights
        self.feature_names = feature_names
        self.calibration = calibration
        self.weight_floor = float(np.clip(weight_floor, 0.0, 1.0))
        self.weight_threshold = float(np.clip(weight_threshold, 0.0, 1.0))
        self.version = version
        self.feature_version = feature_version
        self.available = True
        self.reason = "Model loaded"

    @classmethod
    def load(cls, directory: Path | None = None) -> ReliabilityModel:
        directory = directory or Path(os.environ.get("LOCUS_X_AI_MODEL_DIR", DEFAULT_MODEL_DIR))
        try:
            config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
            arrays = np.load(directory / "weights.npz", allow_pickle=False)
            expected = list(config["feature_names"])
            if expected != FEATURE_NAMES:
                raise ValueError("Model feature schema does not match this engine")
            weights = {name: np.asarray(arrays[name], dtype=float) for name in arrays.files}
            return cls(
                mean=np.asarray(config["feature_mean"], dtype=float),
                scale=np.asarray(config["feature_scale"], dtype=float),
                weights=weights,
                feature_names=expected,
                calibration=(
                    float(config["calibration"]["slope"]),
                    float(config["calibration"]["intercept"]),
                ),
                weight_floor=float(config["weight_policy"]["floor"]),
                weight_threshold=float(config["weight_policy"].get("threshold", 0.0)),
                version=str(config.get("model_version", MODEL_VERSION)),
                feature_version=str(config.get("feature_version", FEATURE_VERSION)),
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            model = cls(
                mean=np.zeros(len(FEATURE_NAMES)),
                scale=np.ones(len(FEATURE_NAMES)),
                weights={},
                feature_names=FEATURE_NAMES,
                calibration=(1.0, 0.0),
                weight_floor=0.08,
            )
            model.available = False
            model.reason = "AI model unavailable; statistical LOCUS-X fallback"
            return model

    def _forward(self, matrix: np.ndarray) -> np.ndarray:
        hidden = np.maximum(0.0, matrix @ self.weights["W1"] + self.weights["b1"])
        hidden = np.maximum(0.0, hidden @ self.weights["W2"] + self.weights["b2"])
        return (hidden @ self.weights["W3"] + self.weights["b3"]).reshape(-1)

    def predict(
        self,
        measurements: list[Measurement],
        environment: Environment,
        estimate: Estimate | None,
        history: FeatureHistory,
    ) -> ReliabilityOutput:
        start = perf_counter()
        if not self.available:
            return self._fallback("AI model unavailable; statistical fallback")
        batch = extract_features(measurements, environment, estimate, history)
        if batch is None:
            return self._fallback("AI features unavailable; statistical fallback")
        normalized = (batch.matrix - self.mean) / self.scale
        logits = self._forward(normalized)
        slope, intercept = self.calibration
        nlos_probabilities = _sigmoid(slope * logits + intercept)
        predictions = []
        for anchor, nlos_probability in zip(batch.anchors, nlos_probabilities, strict=True):
            p_los = float(np.clip(1.0 - nlos_probability, 0.0, 1.0))
            if self.weight_threshold > 0 and p_los >= self.weight_threshold:
                weight = 1.0
            elif self.weight_threshold > 0:
                weight = self.weight_floor + (1.0 - self.weight_floor) * (
                    p_los / self.weight_threshold
                )
            else:
                weight = self.weight_floor + (1.0 - self.weight_floor) * p_los
            predictions.append(
                AIPrediction(
                    anchor_id=anchor,
                    p_los=p_los,
                    weight=float(weight),
                )
            )
        predicted_los = sum(prediction.p_los >= 0.5 for prediction in predictions)
        predicted_nlos = len(predictions) - predicted_los
        ambiguous = predicted_los < 3
        fallback = ambiguous
        reason = (
            "Fewer than three anchors predicted LOS; ambiguous consensus, statistical fallback"
            if ambiguous
            else "AI reliability active"
        )
        return ReliabilityOutput(
            weights=(
                None
                if fallback
                else {prediction.anchor_id: prediction.weight for prediction in predictions}
            ),
            metadata=AIMetadata(
                status="fallback" if fallback else "active",
                reason=reason,
                model_version=self.version,
                feature_version=self.feature_version,
                ambiguous=ambiguous,
                n_predicted_los=predicted_los,
                n_predicted_nlos=predicted_nlos,
                predictions=predictions,
                runtime_ms=(perf_counter() - start) * 1000,
            ),
        )

    def with_policy(self, floor: float, threshold: float) -> ReliabilityModel:
        return ReliabilityModel(
            mean=self.mean,
            scale=self.scale,
            weights=self.weights,
            feature_names=self.feature_names,
            calibration=self.calibration,
            weight_floor=floor,
            weight_threshold=threshold,
            version=self.version,
            feature_version=self.feature_version,
        )

    def _fallback(self, reason: str) -> ReliabilityOutput:
        return ReliabilityOutput(
            weights=None,
            metadata=AIMetadata(
                status="fallback",
                reason=reason,
                model_version=self.version,
                feature_version=self.feature_version,
                ambiguous=False,
                n_predicted_los=0,
                n_predicted_nlos=0,
                predictions=[],
                runtime_ms=0.0,
            ),
        )


def load_default_model() -> ReliabilityModel:
    return ReliabilityModel.load()
