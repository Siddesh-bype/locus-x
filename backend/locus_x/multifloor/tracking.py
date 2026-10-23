"""3D tracking with an explicit floor state machine.

The tracker is the only place where route assistance is allowed to influence the
storey. :class:`MultiFloorLocalizer` in ``inferred`` mode structurally cannot pass
it: the observation bundle carries measurements only and ``update`` rejects an
assistance argument, so the inferred path never reads the simulator's floor.

Honesty rules enforced here:

* A floor change is accepted only while the transition state machine is active, or
  when the implied vertical speed between the two floors is physically possible for
  the elapsed time. Otherwise the previous floor is held and an event is raised.
* While the storey is ambiguous the reported floor is ``None`` -- never the last
  confident value dressed up as a confident one.
* ``updated`` is true only on an accepted measurement update; coasting keeps the
  last position for at most :data:`MAX_COAST_SECONDS`, then withholds it.
"""

from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

import numpy as np

from ..building import Building
from ..schemas import Ellipse, Event, Measurement
from ..tracking import covariance_ellipse
from .coordinates import Anchor3D, anchor_index
from .positioning import (
    FIT_RATIO_LIMIT,
    Estimate3D,
    FloorDecision,
    FloorScores,
    decide_floor,
    floor_scores,
    solve_3d,
)

MAX_COAST_SECONDS = 3.0
"""Same withholding policy as the 2D tracker."""

WHITE_ACCELERATION = 1.2
"""m/s^2 process noise, matching :class:`locus_x.tracking.MotionTracker`."""

INNOVATION_GATE_3D = 16.27
"""99.9% chi-square gate for three degrees of freedom."""

INNOVATION_GATE_2D = 13.82
"""99.9% chi-square gate for two degrees of freedom."""

FLOOR_JUMP_GRACE = 0.5
"""Metres of slack in the vertical-speed anti-jump check."""

HORIZONTAL_ONLY_SIGMA = 0.5
"""Conservative sigma for a horizontal projection whose vertical is unobservable."""


@dataclass(frozen=True, slots=True)
class FloorState:
    """The reported storey, with where it came from and whether it is trusted."""

    floor_id: str | None
    source: Literal["configured", "route_assisted", "inferred"]
    confidence: float | None
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class TransitionState:
    """One vertical traverse, as far as the tracker currently knows it."""

    phase: Literal["entering", "traversing", "exiting", "reacquiring"]
    transition_id: str | None = None
    connection_id: str | None = None
    from_floor_id: str | None = None
    to_floor_id: str | None = None
    progress: float | None = None


@dataclass(frozen=True, slots=True)
class RouteAssist:
    """Route-derived storey hint. Consumed by route-assisted mode only.

    ``floor_id`` is ``None`` while the configured route is between floors, which is
    what lets the state machine distinguish a traverse from a stationary ambiguity.
    """

    floor_id: str | None
    transition_id: str | None = None
    connection_id: str | None = None
    from_floor_id: str | None = None
    to_floor_id: str | None = None
    progress: float | None = None


@dataclass(frozen=True, slots=True)
class Tracking3D:
    """Frame-ready 3D tracking output."""

    position: tuple[float, float, float] | None
    z: float | None
    velocity: tuple[float, float, float] | None
    covariance: list[list[float]] | None
    ellipse: Ellipse | None
    floor_state: FloorState
    transition: TransitionState | None
    status: str
    reason: str
    updated: bool
    outage_seconds: float
    runtime_ms: float


@dataclass(frozen=True, slots=True)
class LocalizerResult:
    """Everything one localizer step produces."""

    estimate: Estimate3D
    scores: FloorScores
    decision: FloorDecision
    tracking: Tracking3D
    events: list[Event] = field(default_factory=list)


def vertical_speed_limit(building: Building) -> float:
    """Fastest configured vertical speed, used as the anti-jump bound.

    Derived from the authored transitions rather than hard-coded: a building whose
    lifts are slower than its stairs is bounded by the faster one, and a building
    with no transition routes falls back to a conservative 1.5 m/s.
    """
    elevations = {floor.id: floor.elevation for floor in building.floors}
    speeds = [
        abs(elevations[transition.to_floor_id] - elevations[transition.from_floor_id])
        / transition.travel_seconds
        for route in building.transition_routes
        for transition in route.transitions
    ]
    return max(speeds, default=1.5)


class FloorAwareTracker:
    """Six-state Kalman filter with a floor state machine on top."""

    def __init__(self, building: Building, *, max_vertical_speed: float | None = None):
        self.building = building
        self.max_vertical_speed = (
            vertical_speed_limit(building) if max_vertical_speed is None else max_vertical_speed
        )
        if self.max_vertical_speed <= 0:
            raise ValueError("Maximum vertical speed must be positive")
        self.state: np.ndarray | None = None
        self.covariance: np.ndarray | None = None
        self.timestamp: float | None = None
        self.last_update: float | None = None
        self.floor_id: str | None = None
        self.floor_since: float | None = None
        self.phase: Literal["idle", "entering", "traversing", "exiting", "reacquiring"] = "idle"
        self.events: list[Event] = []

    def reset(self) -> None:
        self.state = None
        self.covariance = None
        self.timestamp = None
        self.last_update = None
        self.floor_id = None
        self.floor_since = None
        self.phase = "idle"
        self.events = []

    def drain_events(self) -> list[Event]:
        events, self.events = self.events, []
        return events

    def _floor_target(self, floor_id: str) -> float:
        floor = next(floor for floor in self.building.floors if floor.id == floor_id)
        return floor.elevation + self.building.target_device_height

    def _floor_change_allowed(self, previous: str, target: str, timestamp: float) -> bool:
        reference = self.floor_since if self.floor_since is not None else timestamp
        elapsed = max(0.0, timestamp - reference)
        needed = abs(self._floor_target(target) - self._floor_target(previous))
        return needed <= self.max_vertical_speed * elapsed + FLOOR_JUMP_GRACE

    def _advance_phase(self, target: str | None, assist: RouteAssist | None) -> None:
        if assist is not None:
            # Route assistance already knows whether a traverse is in progress, so the
            # phase does not have to be guessed from the absence of a floor.
            if target is None:
                self.phase = "traversing"
            elif self.phase in ("entering", "traversing"):
                self.phase = "exiting"
            elif self.phase == "exiting":
                self.phase = "reacquiring"
            else:
                self.phase = "idle"
            return
        if self.phase in ("entering", "traversing"):
            self.phase = "traversing" if target is None else "exiting"
        elif self.phase == "exiting":
            self.phase = "reacquiring" if target is not None else "traversing"
        else:
            self.phase = "idle" if target is not None else "entering"

    def update(
        self,
        estimate: Estimate3D,
        timestamp: float,
        *,
        decision: FloorDecision | None = None,
        assist: RouteAssist | None = None,
    ) -> Tracking3D:
        start = perf_counter()
        dt = 0.0 if self.timestamp is None else max(0.0, timestamp - self.timestamp)
        self.timestamp = timestamp
        if self.state is not None and self.covariance is not None:
            transition = np.eye(6)
            transition[0, 3] = transition[1, 4] = transition[2, 5] = dt
            acceleration = np.zeros((6, 3))
            acceleration[0:3, 0:3] = np.eye(3) * (dt**2 / 2)
            acceleration[3:6, 0:3] = np.eye(3) * dt
            self.state = transition @ self.state
            self.covariance = (
                transition @ self.covariance @ transition.T
                + WHITE_ACCELERATION**2 * acceleration @ acceleration.T
            )

        updated = False
        reason = estimate.reason
        measurement: np.ndarray | None = None
        measurement_cov: np.ndarray | None = None
        rows = 0
        if estimate.observable and estimate.position is not None and estimate.covariance:
            measurement = np.array(estimate.position)
            measurement_cov = np.array(estimate.covariance) + np.eye(3) * 0.04
            rows = 3
        elif estimate.horizontal is not None:
            measurement = np.array(estimate.horizontal)
            measurement_cov = np.eye(2) * HORIZONTAL_ONLY_SIGMA**2
            rows = 2

        if measurement is not None and measurement_cov is not None:
            expired = (
                self.last_update is not None and timestamp - self.last_update >= MAX_COAST_SECONDS
            )
            if self.state is None or expired:
                self.state = np.zeros(6)
                self.state[:rows] = measurement
                self.covariance = np.zeros((6, 6))
                self.covariance[:rows, :rows] = measurement_cov
                self.covariance[rows:, rows:] = np.eye(6 - rows) * 4
                updated = True
                reason = "Initialized from ranges" if not expired else "Reacquired after outage"
                if expired:
                    self.events.append(
                        Event(
                            timestamp=timestamp,
                            message="Position reacquired after a tracking outage",
                            level="warning",
                        )
                    )
            else:
                observation = np.eye(rows, 6)
                innovation = measurement - observation @ self.state
                innovation_cov = observation @ self.covariance @ observation.T + measurement_cov
                distance = float(innovation @ np.linalg.solve(innovation_cov, innovation))
                gate = INNOVATION_GATE_3D if rows == 3 else INNOVATION_GATE_2D
                if distance < gate:
                    gain = np.linalg.solve(innovation_cov, observation @ self.covariance).T
                    self.state = self.state + gain @ innovation
                    complement = np.eye(6) - gain @ observation
                    self.covariance = (
                        complement @ self.covariance @ complement.T
                        + gain @ measurement_cov @ gain.T
                    )
                    updated = True
                    reason = "Robust 3D range fix + motion update"
                else:
                    reason = "Range fix inconsistent with motion; prediction only"
            if updated:
                self.last_update = timestamp

        floor_state, transition_state = self._update_floor(timestamp, decision, assist)
        outage = 0.0 if self.last_update is None else timestamp - self.last_update
        available = self.state is not None and outage < MAX_COAST_SECONDS
        status = "tracking" if updated else ("degraded" if available else "unavailable")
        if not available and self.state is not None:
            reason = "No accepted update for 3 s; position withheld"
        covariance = self.covariance[:3, :3] if available and self.covariance is not None else None
        position = tuple(self.state[:3]) if available and self.state is not None else None
        return Tracking3D(
            position=position,  # type: ignore[arg-type]
            z=position[2] if position is not None else None,
            velocity=tuple(self.state[3:]) if available and self.state is not None else None,
            covariance=covariance.tolist() if covariance is not None else None,
            ellipse=(covariance_ellipse(covariance[:2, :2]) if covariance is not None else None),
            floor_state=floor_state,
            transition=transition_state,
            status=status,
            reason=reason,
            updated=updated,
            outage_seconds=outage,
            runtime_ms=(perf_counter() - start) * 1000,
        )

    def _update_floor(
        self,
        timestamp: float,
        decision: FloorDecision | None,
        assist: RouteAssist | None,
    ) -> tuple[FloorState, TransitionState | None]:
        source: Literal["configured", "route_assisted", "inferred"]
        target: str | None
        confidence: float | None
        ambiguous: bool
        if assist is not None:
            source = "route_assisted"
            target = assist.floor_id
            confidence = None
            ambiguous = target is None
        elif decision is not None:
            source = decision.source
            target = decision.floor_id
            confidence = decision.confidence
            ambiguous = decision.ambiguous
        else:
            return (
                FloorState(self.floor_id, "configured", None, self.floor_id is None),
                None,
            )

        self._advance_phase(target, assist)
        if (
            target is not None
            and self.floor_id is not None
            and target != self.floor_id
            and self.phase not in ("exiting", "reacquiring", "traversing")
            and not self._floor_change_allowed(self.floor_id, target, timestamp)
        ):
            self.events.append(
                Event(
                    timestamp=timestamp,
                    message=(
                        f"Floor change {self.floor_id} -> {target} rejected: "
                        "inconsistent with the configured vertical speed"
                    ),
                    level="warning",
                )
            )
            target = self.floor_id
            ambiguous = True
            # The rejected hypothesis must not leave its likelihood looking like
            # confidence in the storey that was actually kept.
            confidence = None

        if target is None:
            self.floor_id = None
            self.floor_since = None
        else:
            changed = target != self.floor_id
            self.floor_id = target
            if changed or self.floor_since is None:
                self.floor_since = timestamp

        transition: TransitionState | None = None
        if self.phase != "idle":
            transition = TransitionState(
                phase=self.phase,  # type: ignore[arg-type]
                transition_id=assist.transition_id if assist else None,
                connection_id=assist.connection_id if assist else None,
                from_floor_id=assist.from_floor_id if assist else None,
                to_floor_id=assist.to_floor_id if assist else None,
                progress=assist.progress if assist else None,
            )
        confident = self.floor_id is not None and not ambiguous
        return (
            FloorState(
                self.floor_id,
                source,
                confidence if confident else None,
                ambiguous or self.floor_id is None,
            ),
            transition,
        )


class MultiFloorLocalizer:
    """Measurement -> 3D solve -> floor decision -> tracker update.

    ``inferred`` mode takes measurements only; passing route assistance is an
    error, so the truth floor cannot reach the inference path even by accident.
    ``route_assisted`` mode requires assistance, but the assistance is consumed only
    by the tracker: :func:`floor_scores` is always evaluated over every floor in both
    modes, so route truth cannot narrow, weight or otherwise shape the scores. A
    caller with an inference-only reason to restrict the candidates can call
    ``floor_scores(..., candidate_floors=...)`` directly.
    """

    def __init__(
        self,
        building: Building,
        *,
        mode: Literal["inferred", "route_assisted"],
        device_height: float | None = None,
        robust: bool = True,
    ):
        if mode not in ("inferred", "route_assisted"):
            raise ValueError(f"Unknown localization mode {mode!r}")
        self.building = building
        self.mode = mode
        height = building.target_device_height if device_height is None else device_height
        if height <= 0:
            raise ValueError(f"Device height must be positive, got {height!r}")
        self.device_height = height
        self.anchors: dict[str, Anchor3D] = anchor_index(building)
        self.datums = [floor.elevation for floor in building.floors]
        self.robust = robust
        self.trust: dict[str, float] = {}
        self.tracker = FloorAwareTracker(building)

    def reset(self) -> None:
        self.tracker.reset()
        self.trust.clear()

    def update(
        self,
        observations: dict[str, Measurement],
        *,
        timestamp: float,
        assist: RouteAssist | None = None,
    ) -> LocalizerResult:
        if self.mode == "inferred" and assist is not None:
            raise ValueError(
                "inferred mode cannot receive route assistance: the storey must come "
                "from the ranges alone"
            )
        if self.mode == "route_assisted" and assist is None:
            raise ValueError("route_assisted mode requires route assistance")

        candidates: tuple[str, ...] | None = None
        if assist is not None and assist.floor_id is None:
            pair = (assist.from_floor_id, assist.to_floor_id)
            if pair[0] and pair[1]:
                candidates = (pair[0], pair[1])

        estimate = solve_3d(
            observations,
            self.anchors,
            robust=self.robust,
            trust=self.trust,
            slab_bias_per_slab=self.building.slab_bias_per_slab,
            datums=self.datums,
        )
        scores = floor_scores(
            observations,
            self.building,
            self.anchors,
            device_height=self.device_height,
            noise_sigma=estimate.noise_sigma,
        )
        if assist is not None:
            decision = FloorDecision(
                floor_id=assist.floor_id,
                ambiguous=assist.floor_id is None,
                traversing=assist.floor_id is None,
                reason=(
                    "Route-assisted storey"
                    if assist.floor_id is not None
                    else "Route-assisted traverse; the storey is not claimed mid-flight"
                ),
                source="route_assisted",
                candidates=candidates or (),
            )
        else:
            decision = decide_floor(
                estimate,
                scores,
                self.building,
                device_height=self.device_height,
                candidates=candidates,
            )
        tracking = self.tracker.update(
            estimate,
            timestamp,
            decision=decision,
            assist=assist if self.mode == "route_assisted" else None,
        )
        return LocalizerResult(
            estimate=estimate,
            scores=scores,
            decision=decision,
            tracking=tracking,
            events=self.tracker.drain_events(),
        )


__all__ = [
    "FIT_RATIO_LIMIT",
    "FloorAwareTracker",
    "FloorState",
    "LocalizerResult",
    "MultiFloorLocalizer",
    "RouteAssist",
    "Tracking3D",
    "TransitionState",
    "vertical_speed_limit",
]
