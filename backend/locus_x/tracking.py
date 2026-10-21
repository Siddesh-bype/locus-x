"""Constant-velocity linear Kalman filter over robust position fixes."""

from time import perf_counter

import numpy as np

from .schemas import Ellipse, Estimate, Tracking

CHI2_95_2D = 5.991464547107979
MAX_COAST_SECONDS = 3.0


def covariance_ellipse(covariance: np.ndarray) -> Ellipse:
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]
    radii = np.sqrt(np.maximum(values, 0) * CHI2_95_2D)
    return Ellipse(
        major=float(radii[0]),
        minor=float(radii[1]),
        angle=float(np.arctan2(vectors[1, 0], vectors[0, 0])),
    )


class MotionTracker:
    def __init__(self):
        self.state = None
        self.covariance = None
        self.timestamp = None
        self.last_update = None

    def update(self, estimate: Estimate, timestamp: float) -> Tracking:
        start = perf_counter()
        dt = 0 if self.timestamp is None else max(0, timestamp - self.timestamp)
        self.timestamp = timestamp
        if self.state is not None:
            transition = np.eye(4)
            transition[0, 2] = transition[1, 3] = dt
            # White acceleration, 1.2 m/s²; uncertainty grows during prediction-only intervals.
            acceleration = np.array([[dt**2 / 2, 0], [0, dt**2 / 2], [dt, 0], [0, dt]])
            self.state = transition @ self.state
            self.covariance = (
                transition @ self.covariance @ transition.T + 1.2**2 * acceleration @ acceleration.T
            )
        updated = False
        reason = estimate.reason
        if estimate.position is not None and estimate.covariance is not None:
            measurement = np.array(estimate.position)
            measurement_cov = np.array(estimate.covariance) + np.eye(2) * 0.04
            expired = (
                self.last_update is not None and timestamp - self.last_update >= MAX_COAST_SECONDS
            )
            if self.state is None or expired:
                self.state = np.array([*measurement, 0, 0])
                self.covariance = np.zeros((4, 4))
                self.covariance[:2, :2] = measurement_cov
                self.covariance[2:, 2:] = np.eye(2) * 4
                updated = True
                reason = "Initialized from ranges" if not expired else "Reacquired after outage"
            else:
                observation = np.eye(2, 4)
                innovation = measurement - observation @ self.state
                innovation_cov = observation @ self.covariance @ observation.T + measurement_cov
                distance = float(innovation @ np.linalg.solve(innovation_cov, innovation))
                if distance < 13.82:  # 99.9% 2-D innovation gate; no fault-label input.
                    gain = np.linalg.solve(innovation_cov, observation @ self.covariance).T
                    self.state += gain @ innovation
                    complement = np.eye(4) - gain @ observation
                    self.covariance = (
                        complement @ self.covariance @ complement.T
                        + gain @ measurement_cov @ gain.T
                    )
                    updated = True
                    reason = "Robust range fix + motion update"
                else:
                    reason = "Range fix inconsistent with motion; prediction only"
            if updated:
                self.last_update = timestamp
        outage = 0 if self.last_update is None else timestamp - self.last_update
        available = self.state is not None and outage < MAX_COAST_SECONDS
        status = "tracking" if updated else ("degraded" if available else "unavailable")
        if not available and self.state is not None:
            reason = "No accepted update for 3 s; position withheld"
        covariance = self.covariance[:2, :2] if available else None
        return Tracking(
            position=tuple(self.state[:2]) if available else None,
            velocity=tuple(self.state[2:]) if available else None,
            covariance=covariance.tolist() if covariance is not None else None,
            state_covariance=self.covariance.tolist() if self.covariance is not None else None,
            ellipse=covariance_ellipse(covariance) if covariance is not None else None,
            status=status,
            reason=reason,
            updated=updated,
            outage_seconds=outage,
            runtime_ms=(perf_counter() - start) * 1000,
        )
