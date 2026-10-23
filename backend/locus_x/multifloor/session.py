"""API session for the multi-floor pipeline.

One ``MultiFloorSession`` is the multi-floor counterpart of
:class:`~locus_x.session.SimulationSession`: same ``advance`` / ``update`` / ``reset`` /
``restart`` / ``check_revision`` surface, so ``main.py`` routes the existing endpoints
without a second API. It differs in three deliberate ways:

* It is created from a :class:`~locus_x.building.Building` and one of its
  ``transition_routes``; a storey is never selected by a client. ``select_floor`` only
  raises, which ``main.py`` turns into an honest HTTP 409.
* Every frame is produced by :class:`~locus_x.multifloor.tracking.MultiFloorLocalizer`
  over 3D ranges. Route assistance is derived from the truth trajectory only in
  ``route_assisted`` mode; ``inferred`` mode never receives it.
* Truth is used for exactly two things: the simulator's observations/labels and the
  frame's ``evaluation`` block. It never reaches the localizer as a floor, route or
  position, with the one documented exception of the route-assisted storey hint.

The 2D fields are never faked: ``baseline`` is explicitly ``not_applicable``,
``robust`` carries the 3D solve's horizontal projection plus its 3x3 covariance, and
``tracking`` carries the horizontal state while ``elevation_estimate`` carries ``z``.
"""

from copy import deepcopy
from hashlib import sha256
from math import isfinite
from threading import RLock
from time import monotonic
from uuid import uuid4

from ..building import Building, validate_anchor_keys
from ..schemas import (
    PRESETS,
    Anchor,
    Controls,
    Estimate,
    Evaluation,
    Event,
    Frame,
    Settings,
    Tracking,
)
from .coordinates import anchor_index, anchor_key, floor_anchors
from .evaluation import evaluate_multifloor
from .route import RouteState, route_state
from .simulation import generate_measurements_3d
from .tracking import MultiFloorLocalizer, RouteAssist

LOCALIZATION_MODES = {
    "inferred": "multifloor_inferred",
    "route_assisted": "multifloor_route_assisted",
}
"""Frame ``localization_mode`` for each session mode; byte-stable and additive."""

NOT_APPLICABLE = "The single-floor 2D baseline does not apply to a multi-floor session"
"""``Frame.baseline.reason``. The baseline is not run at all, so its position is null."""


class MultiFloorSession:
    def __init__(
        self,
        building: Building,
        settings: Settings,
        *,
        mode: str,
        route_id: str,
        device_height: float | None = None,
    ):
        if not isinstance(building, Building):
            raise ValueError("Multi-floor sessions require a building environment")
        if mode not in LOCALIZATION_MODES:
            raise ValueError(f"Unknown multi-floor localization mode {mode!r}")
        self.id = str(uuid4())
        self.configuration = building
        self.environment_sha256 = sha256(building.model_dump_json().encode()).hexdigest()
        self.mode = mode
        self.route_id = route_id
        self.device_height = (
            building.target_device_height if device_height is None else device_height
        )
        if not self.device_height > 0:
            raise ValueError(f"Device height must be positive, got {self.device_height!r}")
        self.validate_settings(settings.model_copy(update={"route": route_id}))
        self.initial_mode = mode
        self.initial_route_id = route_id
        self.initial_device_height = self.device_height
        self.initial_settings = settings.model_copy(deep=True, update={"route": route_id})
        self.settings = settings.model_copy(deep=True, update={"route": route_id})
        self.revision = 0
        self.frame_index = 0
        self.timestamp = 0.0
        self.localizer = self._localizer()
        self.events = [
            Event(
                timestamp=0,
                message=(
                    f"Multi-floor session initialized · {building.name} · "
                    f"{route_id} · {mode} · seed {self.settings.seed}"
                ),
            )
        ]
        self.lock = RLock()
        self.touched = monotonic()
        self.frame = self.compute()

    # ------------------------------------------------------------------ config --

    def _routes(self):
        return {route.id: route for route in self.building.transition_routes}

    @property
    def building(self) -> Building:
        return self.configuration

    def _localizer(self) -> MultiFloorLocalizer:
        return MultiFloorLocalizer(
            self.building,
            mode=self.mode,
            device_height=self.device_height,
        )

    def validate_settings(self, settings: Settings) -> None:
        """The same boundary checks a 2D session applies, using building-wide anchors."""
        if len(settings.disabled_anchors) != len(set(settings.disabled_anchors)):
            raise ValueError("Disabled anchor IDs must be unique")
        # Raises for duplicates, malformed keys and unknown floor/anchor pairs.
        validate_anchor_keys(settings.disabled_anchor_keys, anchor_index(self.building))
        if settings.route not in self._routes():
            raise ValueError(
                f"Unknown multi-floor route {settings.route!r}; available routes are "
                f"{sorted(self._routes())}"
            )
        installed = {anchor.id for floor in self.building.floors for anchor in floor.anchors}
        if set(settings.disabled_anchors) - installed:
            raise ValueError("Unknown anchor ID")

    # ------------------------------------------------------------- simulation --

    def _observations(self, measurements, labels) -> dict:
        """Key measurements by ``"<floor_id>:<anchor_id>"`` using building order.

        ``Measurement.anchor_id`` repeats on every storey, so the k-th available label
        in :func:`floor_anchors` order is the k-th measurement. Alignment is checked,
        not assumed: a mismatch would silently file ranges under the wrong floor.
        """
        keys = [
            anchor_key(anchor.floor_id, anchor.anchor_id) for anchor in floor_anchors(self.building)
        ]
        available = [key for key, label in zip(keys, labels, strict=True) if label.available]
        if len(available) != len(measurements):
            raise RuntimeError(
                "Observation alignment broke: "
                f"{len(available)} available labels but {len(measurements)} measurements"
            )
        return dict(zip(available, measurements, strict=True))

    def _assist(self, state: RouteState) -> RouteAssist | None:
        """Route assistance for route-assisted mode only; inferred mode gets none.

        The assist is the only sanctioned path from route truth to the tracker, and
        the localizer refuses it in inferred mode by design.
        """
        if self.mode != "route_assisted":
            return None
        if state.floor_id is not None:
            return RouteAssist(floor_id=state.floor_id)
        if state.transition_id is None:
            raise RuntimeError(
                f"Route state at {state.timestamp!r} s names neither a floor nor a transition"
            )
        transition = next(
            transition
            for transition in self._routes()[self.route_id].transitions
            if transition.id == state.transition_id
        )
        return RouteAssist(
            floor_id=None,
            transition_id=transition.id,
            connection_id=transition.connection_id,
            from_floor_id=transition.from_floor_id,
            to_floor_id=transition.to_floor_id,
            progress=state.progress,
        )

    def compute(self) -> Frame:
        # The filter/trust state before this timestamp, so a controls edit can recompute
        # the same instant without applying the same measurement twice.
        self.before_frame = deepcopy((self.localizer.tracker, self.localizer.trust))
        route = self._routes()[self.route_id]
        truth = route_state(
            self.building,
            route,
            self.timestamp,
            speed=self.settings.speed,
            device_height=self.device_height,
        )
        measurements, labels, _ = generate_measurements_3d(
            self.building, self.settings, truth, self.frame_index
        )
        result = self.localizer.update(
            self._observations(measurements, labels),
            timestamp=self.timestamp,
            assist=self._assist(truth),
        )
        tracking = result.tracking

        previous = getattr(self, "frame", None)
        self.events.extend(result.events)
        if previous and previous.tracking.status != tracking.status:
            self.events.append(
                Event(
                    timestamp=self.timestamp,
                    message=tracking.reason,
                    level="info" if tracking.updated else "warning",
                )
            )
        previous_outages = (
            {label.anchor_id for label in previous.simulator_labels if label.kind == "outage"}
            if previous
            else set()
        )
        outages = {label.anchor_id for label in labels if label.kind == "outage"}
        if outages != previous_outages:
            self.events.append(
                Event(
                    timestamp=self.timestamp,
                    message=f"Timed outage: {', '.join(sorted(outages))}"
                    if outages
                    else "Timed outage recovered",
                    level="warning" if outages else "info",
                )
            )
        self.events = self.events[-20:]
        self.touched = monotonic()

        report = evaluate_multifloor(truth, tracking, result.scores, truth.z)
        floor_id = tracking.floor_state.floor_id
        floor_elevation = next(
            (floor.elevation for floor in self.building.floors if floor.id == floor_id),
            None,
        )
        probabilities = {
            floor: float(probability)
            for floor, probability in result.scores.probability.items()
            if isfinite(probability)
        }
        estimate = result.estimate
        position = (
            None if tracking.position is None else (tracking.position[0], tracking.position[1])
        )
        velocity = (
            None if tracking.velocity is None else (tracking.velocity[0], tracking.velocity[1])
        )
        covariance = (
            None if tracking.covariance is None else [row[:2] for row in tracking.covariance[:2]]
        )
        return Frame(
            session_id=self.id,
            environment_id=self.building.id,
            environment_version=self.building.version,
            environment_sha256=self.environment_sha256,
            floor_id=floor_id,
            floor_elevation=floor_elevation,
            localization_mode=LOCALIZATION_MODES[self.mode],
            revision=self.revision,
            frame_index=self.frame_index,
            timestamp=self.timestamp,
            settings=self.settings,
            # Building order: floors in declaration order, each floor's anchors in
            # declaration order. Measurement/label indices align with this list.
            anchors=[
                Anchor(id=anchor.anchor_id, position=(anchor.x, anchor.y))
                for anchor in floor_anchors(self.building)
            ],
            measurements=measurements,
            simulator_labels=labels,
            baseline=Estimate(
                position=None,
                status="not_applicable",
                reason=NOT_APPLICABLE,
                runtime_ms=0.0,
            ),
            robust=Estimate(
                position=estimate.horizontal,
                status=estimate.status,
                reason=estimate.reason,
                residuals=estimate.residuals,
                reliability=estimate.reliability,
                covariance=estimate.covariance,
                runtime_ms=estimate.runtime_ms,
                condition_number=estimate.condition_number,
            ),
            tracking=Tracking(
                position=position,
                velocity=velocity,
                covariance=covariance,
                state_covariance=None,
                ellipse=tracking.ellipse,
                status=tracking.status,
                reason=tracking.reason,
                updated=tracking.updated,
                outage_seconds=tracking.outage_seconds,
                runtime_ms=tracking.runtime_ms,
            ),
            evaluation=Evaluation(
                ground_truth=(truth.x, truth.y),
                baseline_error=None,
                proposed_error=report.horizontal_error,
                covered_95=report.covered_horizontal_95,
                has_nlos=any(label.kind == "nlos" for label in labels),
                has_dropout=any(label.kind in ("outage", "packet_loss") for label in labels),
                ground_truth_elevation=truth.z,
            ),
            events=self.events,
            floor_state=tracking.floor_state,
            transition=tracking.transition,
            elevation_estimate=tracking.z,
            floor_probabilities=probabilities or None,
        )

    # ------------------------------------------------------------------ session --

    def check_revision(self, expected: int) -> None:
        if expected != self.revision:
            raise RuntimeError("Stale revision; refresh the session before retrying")

    def advance(self, dt: float = 0.2) -> Frame:
        self.timestamp = round(self.timestamp + dt, 8)
        self.frame_index += 1
        self.revision += 1
        self.frame = self.compute()
        return self.frame

    def reset(self) -> Frame:
        """Restore the mode, route, device height and settings captured at creation."""
        self.mode = self.initial_mode
        self.route_id = self.initial_route_id
        self.device_height = self.initial_device_height
        self.settings = self.initial_settings.model_copy(deep=True)
        return self.restart()

    def restart(self) -> Frame:
        """Restart time and history while keeping newly edited settings."""
        self.timestamp = 0.0
        self.frame_index = 0
        self.revision += 1
        self.localizer = self._localizer()
        self.events = [
            Event(
                timestamp=0,
                message=(
                    f"Multi-floor run initialized · {self.building.name} · "
                    f"{self.route_id} · {self.mode} · seed {self.settings.seed}"
                ),
            )
        ]
        # The previous run's fault state must not generate spurious recovery events.
        self.frame = None
        self.frame = self.compute()
        return self.frame

    def update(self, controls: Controls) -> Frame:
        updates = controls.model_dump(exclude_none=True, exclude={"expected_revision"})
        route_id = updates.pop("route", None)
        if "scenario" in updates:
            updates = {**PRESETS[updates["scenario"]], **updates}
        settings = self.settings.model_copy(update=updates)
        if route_id is not None:
            settings = settings.model_copy(update={"route": route_id})
        self.validate_settings(settings)
        next_route = self.route_id if route_id is None else route_id
        restart = next_route != self.route_id or any(
            getattr(settings, key) != getattr(self.settings, key) for key in ("speed", "seed")
        )
        self.settings = settings
        self.route_id = next_route
        if restart:
            return self.restart()
        self.revision += 1
        # Recompute this timestamp without double-counting filter or trust history.
        self.localizer.tracker, self.localizer.trust = deepcopy(self.before_frame)
        changed = [*updates, *(["route"] if route_id is not None else [])]
        self.events.append(
            Event(timestamp=self.timestamp, message="Controls updated · " + ", ".join(changed))
        )
        self.frame = self.compute()
        return self.frame

    def select_floor(self, floor_id: str) -> Frame:
        """Refuse: a multi-floor storey is inferred or route-assisted, never selected."""
        raise RuntimeError(
            "A multi-floor storey is derived, not selected; the floor endpoint is "
            "unavailable for this session"
        )
