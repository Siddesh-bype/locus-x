"""Public API contracts. Estimator inputs are deliberately smaller than simulation frames."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Point = tuple[float, float]
Scenario = Literal["clean", "noise", "high_noise", "nlos", "packet_loss", "anchor_failure", "chaos"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Anchor(StrictModel):
    id: str
    position: Point


class Wall(StrictModel):
    id: str
    a: Point
    b: Point
    height: float = Field(gt=0)


class Room(StrictModel):
    name: str
    center: Point
    size: Point
    kind: str


class Waypoint(StrictModel):
    position: Point
    dwell: float = Field(ge=0)


class Environment(StrictModel):
    id: str
    name: str
    description: str
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    anchors: list[Anchor]
    walls: list[Wall]
    rooms: list[Room]
    routes: dict[str, list[Waypoint]]

    @model_validator(mode="after")
    def validate_geometry(self):
        if len({a.id for a in self.anchors}) != len(self.anchors):
            raise ValueError("Anchor IDs must be unique")
        if not self.routes or any(len(route) < 2 for route in self.routes.values()):
            raise ValueError("Each route needs at least two waypoints")
        points = [a.position for a in self.anchors]
        points += [p for w in self.walls for p in (w.a, w.b)]
        points += [w.position for route in self.routes.values() for w in route]
        if any(not (0 <= x <= self.width and 0 <= y <= self.height) for x, y in points):
            raise ValueError("Geometry must lie within the floor dimensions")
        return self


class Settings(StrictModel):
    @model_validator(mode="before")
    @classmethod
    def apply_preset_defaults(cls, data):
        scenario = data.get("scenario") if isinstance(data, dict) else None
        if isinstance(scenario, str) and scenario in PRESETS:
            return {**PRESETS[scenario], **data}
        return data

    scenario: Scenario = "nlos"
    noise_std: float = Field(default=0.15, ge=0, le=3)
    nlos_bias: float = Field(default=2.5, ge=0, le=10)
    dropout: float = Field(default=0, ge=0, le=1)
    disabled_anchors: list[str] = Field(default_factory=list, max_length=32)
    # "<floor_id>:<anchor_id>" disables one anchor; disabled_anchors keeps its legacy
    # "this ID on every floor" meaning.
    disabled_anchor_keys: list[str] = Field(default_factory=list, max_length=32)
    walls_enabled: bool = True
    route: str = "asset_round"
    speed: float = Field(default=1.2, ge=0.2, le=3)
    seed: int = Field(default=12, ge=0, le=2**31 - 1)


PRESETS: dict[str, dict] = {
    "clean": {"noise_std": 0, "nlos_bias": 0, "dropout": 0},
    "noise": {"noise_std": 0.4, "nlos_bias": 0, "dropout": 0},
    "high_noise": {"noise_std": 1.2, "nlos_bias": 0, "dropout": 0},
    "nlos": {"noise_std": 0.15, "nlos_bias": 2.5, "dropout": 0, "walls_enabled": True},
    "packet_loss": {"noise_std": 0.2, "nlos_bias": 0, "dropout": 0.4},
    "anchor_failure": {"noise_std": 0.2, "nlos_bias": 0, "dropout": 0},
    "chaos": {"noise_std": 0.65, "nlos_bias": 4, "dropout": 0.25, "walls_enabled": True},
}


class MultifloorOptions(StrictModel):
    """Opt-in multi-floor session. Absent means the legacy 2D session, unchanged."""

    mode: Literal["route_assisted", "inferred"]
    route_id: str = Field(min_length=1)
    device_height: float | None = Field(default=None, gt=0, lt=4)


class SessionCreate(StrictModel):
    environment_id: str = "hospital_floor_01"
    floor_id: str | None = None
    settings: Settings = Field(default_factory=Settings)
    multifloor: MultifloorOptions | None = None


class Controls(StrictModel):
    expected_revision: int = Field(ge=0)
    scenario: Scenario | None = None
    noise_std: float | None = Field(default=None, ge=0, le=3)
    nlos_bias: float | None = Field(default=None, ge=0, le=10)
    dropout: float | None = Field(default=None, ge=0, le=1)
    disabled_anchors: list[str] | None = Field(default=None, max_length=32)
    disabled_anchor_keys: list[str] | None = Field(default=None, max_length=32)
    walls_enabled: bool | None = None
    route: str | None = None
    speed: float | None = Field(default=None, ge=0.2, le=3)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class Advance(StrictModel):
    expected_revision: int = Field(ge=0)
    dt: float = Field(default=0.2, ge=0.05, le=1)


class Revision(StrictModel):
    expected_revision: int = Field(ge=0)


class FloorSelection(Revision):
    floor_id: str = Field(min_length=1)


class Measurement(StrictModel):
    """The entire estimator input surface: no truth, wall labels, or fault flags."""

    anchor_id: str
    anchor_position: Point
    timestamp: float
    observed_range: float = Field(gt=0)


class FaultLabel(StrictModel):
    anchor_id: str
    available: bool
    kind: Literal["los", "nlos", "disabled", "outage", "packet_loss"]
    intersected_walls: list[str]
    true_range: float
    injected_bias: float


class Estimate(StrictModel):
    position: Point | None = None
    status: str
    reason: str
    residuals: dict[str, float] = Field(default_factory=dict)
    reliability: dict[str, float] = Field(default_factory=dict)
    covariance: list[list[float]] | None = None
    runtime_ms: float = 0
    condition_number: float | None = None


class Ellipse(StrictModel):
    major: float
    minor: float
    angle: float
    level: float = 0.95


class Tracking(StrictModel):
    position: Point | None
    velocity: Point | None
    covariance: list[list[float]] | None
    state_covariance: list[list[float]] | None
    ellipse: Ellipse | None
    status: str
    reason: str
    updated: bool
    outage_seconds: float
    runtime_ms: float


class FloorState(StrictModel):
    """The reported storey, with where it came from and whether it is trusted.

    ``source`` is the only honest answer to "why is this the floor?": ``configured``
    means geometry selected it before the run, ``route_assisted`` means the route said
    so, ``inferred`` means the ranges decided. ``floor_id`` is ``None`` while the
    storey is ambiguous, and an ambiguous frame never carries confidence.
    """

    floor_id: str | None
    source: Literal["configured", "route_assisted", "inferred"]
    confidence: float | None
    ambiguous: bool


class TransitionState(StrictModel):
    """One vertical traverse, as far as the tracker currently knows it.

    Only the tracker names a phase; the connector and target are configuration, and
    an inferred-mode traverse carries no ids because no route witness is consulted.
    """

    phase: Literal["entering", "traversing", "exiting", "reacquiring"]
    transition_id: str | None = None
    connection_id: str | None = None
    from_floor_id: str | None = None
    to_floor_id: str | None = None
    progress: float | None = None


class Evaluation(StrictModel):
    ground_truth: Point
    baseline_error: float | None
    proposed_error: float | None
    covered_95: bool | None
    has_nlos: bool
    has_dropout: bool
    # Device elevation of the truth target. Legacy 2D sessions leave it null because
    # their truth is a floor-plane point; multi-floor sessions fill it so a truth
    # marker can be drawn at its real height without guessing.
    ground_truth_elevation: float | None = None


class Event(StrictModel):
    timestamp: float
    message: str
    level: Literal["info", "warning"] = "info"


class Frame(StrictModel):
    session_id: str
    environment_id: str
    environment_version: str
    environment_sha256: str
    # None only for a multi-floor frame whose storey is ambiguous; legacy sessions
    # always populate both.
    floor_id: str | None
    floor_elevation: float | None
    localization_mode: Literal[
        "2d_on_known_floor", "multifloor_route_assisted", "multifloor_inferred"
    ] = "2d_on_known_floor"
    revision: int
    frame_index: int
    timestamp: float
    settings: Settings
    anchors: list[Anchor]
    measurements: list[Measurement]
    simulator_labels: list[FaultLabel]
    baseline: Estimate
    robust: Estimate
    tracking: Tracking
    evaluation: Evaluation
    events: list[Event]
    # Additive, null for every legacy frame: no existing key or value changes.
    floor_state: FloorState | None = None
    transition: TransitionState | None = None
    elevation_estimate: float | None = None
    floor_probabilities: dict[str, float] | None = None


class BenchmarkRequest(StrictModel):
    environment_id: str = "hospital_floor_01"
    floor_id: str | None = None
    scenarios: list[Scenario] = Field(
        default_factory=lambda: list(PRESETS), min_length=1, max_length=7
    )
    anchor_counts: list[Literal[3, 4, 6, 8]] = Field(
        default_factory=lambda: [3, 4, 6, 8], min_length=1, max_length=4
    )
    seeds: list[int] = Field(default_factory=lambda: [101, 202], min_length=1, max_length=4)
    steps: int = Field(default=160, ge=10, le=400)
    dt: float = Field(default=0.5, ge=0.1, le=1)
    route: Literal["asset_round", "held_out"] = "held_out"

    @model_validator(mode="after")
    def bound_work(self):
        if any(seed < 0 or seed > 2**31 - 1 for seed in self.seeds):
            raise ValueError("Seeds must be unsigned 31-bit integers")
        if len(self.scenarios) * len(self.anchor_counts) * len(self.seeds) * self.steps > 25000:
            raise ValueError("Limit benchmark to 25,000 frames per run")
        return self
