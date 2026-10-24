from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

import numpy as np

from .ai_policy import candidate_from_policy, load_policy_file, run_v2_policy
from .building import Building
from .environment import floor_elevation, resolve_floor, route_position
from .estimators import solve
from .evaluation import evaluate
from .reliability import FeatureHistory, load_default_model
from .reliability_v2 import FeatureHistoryV2, ReliabilityV2Model
from .schemas import PRESETS, Controls, Environment, Event, Frame, Settings
from .simulation import generate_measurements
from .tracking import DEFAULT_GATE_CHI2, MotionTracker

AI_MODEL = load_default_model()
_V2_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "ai-reliability-v2"
_V2_POLICY_PATH = _V2_MODEL_DIR / "policy-v2.json"
_V2_MODEL = ReliabilityV2Model.load(_V2_MODEL_DIR) if _V2_MODEL_DIR.exists() else None
_V2_POLICY = load_policy_file(_V2_POLICY_PATH) if _V2_POLICY_PATH.exists() else None
_V2_CANDIDATE = candidate_from_policy(_V2_POLICY) if _V2_POLICY else None


class SimulationSession:
    def __init__(
        self, environment: Environment | Building, settings: Settings, floor_id: str | None = None
    ):
        self.id = str(uuid4())
        self.configuration = environment
        self.environment = resolve_floor(environment, floor_id)
        self.initial_floor_id = self.environment.id
        self.environment_sha256 = sha256(environment.model_dump_json().encode()).hexdigest()
        self.validate_settings(settings)
        self.initial_settings = settings.model_copy(deep=True)
        self.settings = settings.model_copy(deep=True)
        self.revision = 0
        self.frame_index = 0
        self.timestamp = 0.0
        self.tracker = MotionTracker()
        self.trust = {}
        self.ai_tracker = MotionTracker()
        self.ai_features = FeatureHistory()
        self.v2_features = FeatureHistoryV2()
        self.ai_model = AI_MODEL
        self.events = [Event(timestamp=0, message=f"Session initialized · seed {settings.seed}")]
        self.lock = RLock()
        self.touched = monotonic()
        self.frame = self.compute()

    def validate_settings(self, settings):
        if len(settings.disabled_anchors) != len(set(settings.disabled_anchors)):
            raise ValueError("Disabled anchor IDs must be unique")
        if settings.route not in self.environment.routes:
            raise ValueError("Unknown target route")
        if set(settings.disabled_anchors) - {a.id for a in self.environment.anchors}:
            raise ValueError("Unknown anchor ID")

    def compute(self):
        self.before_frame = deepcopy(
            (self.tracker, self.trust, self.ai_tracker, self.ai_features, self.v2_features)
        )
        truth = route_position(
            self.environment.routes[self.settings.route], self.timestamp, self.settings.speed
        )
        measurements, labels = generate_measurements(
            self.environment, self.settings, truth, self.timestamp, self.frame_index
        )
        # Explicitly identical immutable-by-convention observations for every method.
        baseline = solve(measurements)
        trust_before = dict(self.trust)
        robust = solve(measurements, robust=True, trust=self.trust)
        # Same inputs the benchmark gives v2: the statistical fix and the *pre-update*
        # statistical tracker state.
        stat_state = None if self.tracker.state is None else np.array(self.tracker.state)
        stat_timestamp = self.tracker.timestamp
        tracking = self.tracker.update(robust, self.timestamp)
        gate = DEFAULT_GATE_CHI2
        if _V2_MODEL is not None and _V2_MODEL.available and _V2_CANDIDATE is not None:
            _, bias_by_anchor = self.ai_model.predict_full(
                measurements, self.environment, robust, self.ai_features
            )
            result = run_v2_policy(
                v2_model=_V2_MODEL,
                policy=_V2_POLICY,
                candidate=_V2_CANDIDATE,
                measurements=measurements,
                environment=self.environment,
                stat_fix=robust,
                v2_history=self.v2_features,
                tracker_state=stat_state,
                tracker_timestamp=stat_timestamp,
                bias_by_anchor=bias_by_anchor,
            )
            ai, ai_metadata, gate = result.estimate, result.metadata, result.gate
        else:
            ai_output = self.ai_model.predict(
                measurements, self.environment, baseline, self.ai_features
            )
            ai_metadata = ai_output.metadata
            if ai_output.weights is not None:
                ai = solve(
                    measurements,
                    robust=True,
                    trust=trust_before,
                    ai_weights=dict(ai_output.weights),
                )
            else:
                ai = robust
        ai_tracking = self.ai_tracker.update(ai, self.timestamp, gate=gate)
        previous = getattr(self, "frame", None)
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
        return Frame(
            session_id=self.id,
            environment_id=self.configuration.id,
            environment_version=(
                self.configuration.version if isinstance(self.configuration, Building) else "1.0"
            ),
            environment_sha256=self.environment_sha256,
            floor_id=self.environment.id,
            floor_elevation=floor_elevation(self.environment),
            revision=self.revision,
            frame_index=self.frame_index,
            timestamp=self.timestamp,
            settings=self.settings,
            anchors=self.environment.anchors,
            measurements=measurements,
            simulator_labels=labels,
            baseline=baseline,
            robust=robust,
            tracking=tracking,
            ai=ai,
            ai_tracking=ai_tracking,
            ai_metadata=ai_metadata,
            evaluation=evaluate(truth, baseline, tracking, labels, ai, ai_tracking),
            events=self.events,
        )

    def check_revision(self, expected):
        if expected != self.revision:
            raise RuntimeError("Stale revision; refresh the session before retrying")

    def advance(self, dt=0.2):
        self.timestamp = round(self.timestamp + dt, 8)
        self.frame_index += 1
        self.revision += 1
        self.frame = self.compute()
        return self.frame

    def reset(self):
        """Restore the settings captured when this session was created."""
        self.environment = resolve_floor(self.configuration, self.initial_floor_id)
        self.settings = self.initial_settings.model_copy(deep=True)
        return self.restart()

    def select_floor(self, floor_id: str):
        """A manual floor selection starts a separate 2-D run; no state crosses floors."""
        selected = resolve_floor(self.configuration, floor_id)
        if selected.id == self.environment.id:
            return self.frame
        updates = {
            "route": self.settings.route
            if self.settings.route in selected.routes
            else next(iter(selected.routes)),
            "disabled_anchors": [
                a for a in self.settings.disabled_anchors if a in {s.id for s in selected.anchors}
            ],
        }
        self.environment = selected
        self.settings = self.settings.model_copy(update=updates)
        return self.restart()

    def restart(self):
        """Restart time/history while keeping a newly edited route, speed or seed."""
        self.timestamp = 0.0
        self.frame_index = 0
        self.revision += 1
        self.tracker = MotionTracker()
        self.trust = {}
        self.ai_tracker = MotionTracker()
        self.ai_features = FeatureHistory()
        self.v2_features = FeatureHistoryV2()
        self.events = [
            Event(
                timestamp=0,
                message=f"Run initialized · {self.environment.name} · seed {self.settings.seed}",
            )
        ]
        # The previous run's fault state must not generate spurious recovery events.
        self.frame = None
        self.frame = self.compute()
        return self.frame

    def update(self, controls: Controls):
        updates = controls.model_dump(exclude_none=True, exclude={"expected_revision"})
        if "scenario" in updates:
            updates = {**PRESETS[updates["scenario"]], **updates}
        settings = self.settings.model_copy(update=updates)
        self.validate_settings(settings)
        restart = any(
            getattr(settings, key) != getattr(self.settings, key)
            for key in ("route", "speed", "seed")
        )
        self.settings = settings
        if restart:
            return self.restart()
        self.revision += 1
        # Recompute this timestamp without double-counting filter, trust, or feature history.
        (
            self.tracker,
            self.trust,
            self.ai_tracker,
            self.ai_features,
            self.v2_features,
        ) = deepcopy(self.before_frame)
        self.events.append(
            Event(timestamp=self.timestamp, message="Controls updated · " + ", ".join(updates))
        )
        self.frame = self.compute()
        return self.frame
