from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, model_validator

from .benchmark import run_benchmark
from .building import Building
from .environment import load_environments, resolve_floor
from .multifloor.session import MultiFloorSession
from .optimization import (
    OptimizerConfig,
    PlacementPolicy,
    candidate_sites,
    layout_floor,
    run_optimization,
    validate_environment_config,
)
from .schemas import (
    PRESETS,
    Advance,
    BenchmarkRequest,
    Controls,
    Environment,
    FloorSelection,
    Frame,
    Revision,
    SessionCreate,
    StrictModel,
)
from .session import SimulationSession

environments = load_environments()
sessions: dict[str, SimulationSession | MultiFloorSession] = {}
jobs: dict[str, dict] = {}
store_lock = RLock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="benchmark")


@asynccontextmanager
async def lifespan(app):
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="LOCUS-X", version="3.0.0", lifespan=lifespan)


class EnvironmentList(StrictModel):
    environments: list[Environment | Building]
    presets: dict[str, dict]
    coordinate_system: str


class BenchmarkJob(StrictModel):
    id: str
    status: str
    progress: float
    result: dict | None = None
    error: str | None = None


class LayoutJob(StrictModel):
    id: str
    status: str
    progress: float
    stage: str = "queued"
    result: dict | None = None
    error: str | None = None


class LayoutSitesResponse(StrictModel):
    environment_id: str
    floor_id: str
    source: str
    policy: dict
    sites: list[dict]


class LayoutOptimizeRequest(StrictModel):
    environment_id: str = "hospital_floor_01"
    floor_id: str | None = None
    budgets: list[int] = Field(default_factory=lambda: [8], min_length=1, max_length=4)
    with_locus_estimator: bool = True
    development_steps: int | None = Field(default=None, ge=4, le=400)
    validation_steps: int | None = Field(default=None, ge=4, le=400)
    held_out_steps: int | None = Field(default=None, ge=4, le=400)

    @model_validator(mode="after")
    def bounded(self):
        if any(budget < 1 or budget > 16 for budget in self.budgets):
            raise ValueError("Layout budgets must be between 1 and 16 anchors")
        if len(set(self.budgets)) != len(self.budgets):
            raise ValueError("Layout budgets must be unique")
        return self


class EnvironmentRegister(StrictModel):
    environment: dict = Field(
        description="Full Environment or Building JSON to validate and register"
    )


layout_jobs: dict[str, dict] = {}


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": "3.0.0",
        "engine": "numpy/scipy · robust ranges + Kalman tracking",
    }


@app.get("/api/v1/environments", response_model=EnvironmentList)
def list_environments():
    return {
        "environments": list(environments.values()),
        "presets": PRESETS,
        "coordinate_system": "Metres: (x,y) maps to Three.js (x,elevation,-y).",
    }


def session_by_id(session_id):
    with store_lock:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Session not found; create a new session")
        session.touched = monotonic()
        return session


@app.post("/api/v1/sessions", response_model=Frame, status_code=201)
def create_session(request: SessionCreate):
    environment = environments.get(request.environment_id)
    if environment is None:
        raise HTTPException(404, "Unknown environment")
    try:
        if request.multifloor is None:
            session = SimulationSession(environment, request.settings, request.floor_id)
        else:
            session = _multifloor_session(environment, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with store_lock:
        for key in list(sessions):
            if monotonic() - sessions[key].touched > 3600:
                del sessions[key]
        if len(sessions) >= 64:
            raise HTTPException(429, "Local session capacity reached; delete unused sessions")
        sessions[session.id] = session
    return session.frame


def _multifloor_session(environment, request: SessionCreate) -> MultiFloorSession:
    """Validate the multi-floor opt-in before a session object exists (422 on refusal)."""
    if not isinstance(environment, Building):
        raise ValueError("Multi-floor sessions require a building environment")
    if request.floor_id is not None:
        raise ValueError(
            "A multi-floor storey is derived, not selected; omit floor_id and let the "
            "ranges (or the configured route) report it"
        )
    options = request.multifloor
    routes = {route.id for route in environment.transition_routes}
    if options.route_id not in routes:
        raise ValueError(
            f"Unknown multi-floor route {options.route_id!r}; available routes are {sorted(routes)}"
        )
    return MultiFloorSession(
        environment,
        request.settings,
        mode=options.mode,
        route_id=options.route_id,
        device_height=options.device_height,
    )


@app.get("/api/v1/sessions/{session_id}", response_model=Frame)
def get_session(session_id: str):
    session = session_by_id(session_id)
    with session.lock:
        return session.frame


@app.delete("/api/v1/sessions/{session_id}", status_code=204)
def delete_session(session_id: str):
    with store_lock:
        sessions.pop(session_id, None)


def mutate(session_id, request, operation):
    session = session_by_id(session_id)
    with session.lock:
        try:
            session.check_revision(request.expected_revision)
            return operation(session)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


@app.post("/api/v1/sessions/{session_id}/advance", response_model=Frame)
def advance_session(session_id: str, request: Advance):
    return mutate(session_id, request, lambda session: session.advance(request.dt))


@app.patch("/api/v1/sessions/{session_id}/controls", response_model=Frame)
def controls(session_id: str, request: Controls):
    return mutate(session_id, request, lambda session: session.update(request))


@app.post("/api/v1/sessions/{session_id}/reset", response_model=Frame)
def reset(session_id: str, request: Revision):
    return mutate(session_id, request, lambda session: session.reset())


@app.post("/api/v1/sessions/{session_id}/floor", response_model=Frame)
def select_floor(session_id: str, request: FloorSelection):
    session = session_by_id(session_id)
    if isinstance(session, MultiFloorSession):
        # Never let a client force a floor: the reported storey is derived from ranges
        # (inferred) or the configured route (route assisted), both of which are
        # documented, auditable sources. A client-chosen floor would be neither.
        raise HTTPException(
            409,
            "A multi-floor storey is derived, not selected; the floor endpoint is "
            "unavailable for this session",
        )
    return mutate(session_id, request, lambda session: session.select_floor(request.floor_id))


@app.post("/api/v1/benchmarks", response_model=BenchmarkJob, status_code=202)
def start_benchmark(request: BenchmarkRequest):
    environment = environments.get(request.environment_id)
    if environment is None:
        raise HTTPException(404, "Unknown environment")
    try:
        floor = resolve_floor(environment, request.floor_id)
        if request.route not in floor.routes:
            raise ValueError("Unknown target route")
        if not {f"A{i}" for i in range(1, 9)} <= {a.id for a in floor.anchors}:
            raise ValueError("This benchmark protocol requires installed anchors A1–A8")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with store_lock:
        if any(job["status"] in ("queued", "running") for job in jobs.values()):
            raise HTTPException(409, "A benchmark is already running")
        while len(jobs) >= 5:
            del jobs[next(iter(jobs))]
        job = {"id": str(uuid4()), "status": "queued", "progress": 0, "result": None, "error": None}
        jobs[job["id"]] = job

    def progress(value):
        with store_lock:
            job["progress"] = value

    def work():
        try:
            with store_lock:
                job["status"] = "running"
            result = run_benchmark(request, environment=environment, progress=progress)
            with store_lock:
                job.update(status="complete", result=result)
        except Exception:
            with store_lock:
                job.update(status="failed", error="Benchmark failed; inspect the backend log")
            import logging

            logging.exception("Benchmark failure")

    executor.submit(work)
    return job.copy()


@app.get("/api/v1/benchmarks/{job_id}", response_model=BenchmarkJob)
def get_benchmark(job_id: str):
    with store_lock:
        if job_id not in jobs:
            raise HTTPException(404, "Benchmark not found")
        return jobs[job_id].copy()


# --- Anchor layout optimizer -------------------------------------------------


def _layout_environment(environment_id: str, floor_id: str | None):
    environment = environments.get(environment_id)
    if environment is None:
        raise HTTPException(404, "Unknown environment")
    try:
        resolved = layout_floor(environment, floor_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return environment, resolved


@app.get("/api/v1/layouts/sites", response_model=LayoutSitesResponse)
def layout_sites(environment_id: str, floor_id: str | None = None):
    """Candidate anchor sites (with legality) for one floor."""
    environment, resolved = _layout_environment(environment_id, floor_id)
    policy = PlacementPolicy()
    try:
        _, records, source = candidate_sites(environment, resolved, policy)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return LayoutSitesResponse(
        environment_id=environment_id,
        floor_id=resolved,
        source=source,
        policy=policy.model_dump(),
        sites=records,
    )


@app.post("/api/v1/layouts/optimize", response_model=LayoutJob, status_code=202)
def start_layout_optimize(request: LayoutOptimizeRequest):
    """Queue a deterministic placement run (search → validation → comparison)."""
    environment = environments.get(request.environment_id)
    if environment is None:
        raise HTTPException(404, "Unknown environment")
    try:
        resolved = layout_floor(environment, request.floor_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with store_lock:
        if any(job["status"] in ("queued", "running") for job in layout_jobs.values()):
            raise HTTPException(409, "A layout optimization is already running")
        while len(layout_jobs) >= 5:
            del layout_jobs[next(iter(layout_jobs))]
        job = {
            "id": str(uuid4()),
            "status": "queued",
            "progress": 0.0,
            "stage": "queued",
            "result": None,
            "error": None,
        }
        layout_jobs[job["id"]] = job

    def progress(stage: str):
        with store_lock:
            job["stage"] = stage
            job["progress"] = min(0.99, job["progress"] + 0.02)

    def work():
        try:
            with store_lock:
                job["status"] = "running"
                job["stage"] = "search"
            config = OptimizerConfig()
            overrides = {
                "development_split": request.development_steps,
                "validation_split": request.validation_steps,
                "held_out_split": request.held_out_steps,
            }
            for field_name, steps in overrides.items():
                if steps is not None:
                    split = getattr(config, field_name)
                    config = config.model_copy(
                        update={field_name: split.model_copy(update={"steps": steps})}
                    )
            result = run_optimization(
                environment_id=request.environment_id,
                floor_id=resolved,
                budgets=request.budgets,
                config=config,
                with_locus_estimator=request.with_locus_estimator,
                catalog=environments,
                output=None,
                progress=progress,
            )
            with store_lock:
                job.update(status="complete", stage="done", progress=1.0, result=result)
        except Exception:
            import logging

            logging.exception("Layout optimization failure")
            with store_lock:
                job.update(
                    status="failed",
                    stage="failed",
                    error="Layout optimization failed; inspect the backend log",
                )

    executor.submit(work)
    return job.copy()


@app.get("/api/v1/layouts/optimize/{job_id}", response_model=LayoutJob)
def get_layout_job(job_id: str):
    with store_lock:
        if job_id not in layout_jobs:
            raise HTTPException(404, "Layout job not found")
        return layout_jobs[job_id].copy()


@app.post("/api/v1/environments", status_code=201)
def register_environment(request: EnvironmentRegister):
    """Validate and register an environment/Building in memory (never persisted)."""
    raw = request.environment
    try:
        model = validate_environment_config(raw)
    except Exception as error:
        raise HTTPException(422, f"Invalid environment configuration: {error}") from error
    if model.id in environments:
        raise HTTPException(409, f"Environment ID already registered: {model.id}")
    with store_lock:
        environments[model.id] = model
    return {"id": model.id, "name": model.name, "schema": type(model).__name__}


DIST = Path(__file__).resolve().parents[2] / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")

    @app.get("/locus.svg")
    def icon():
        return FileResponse(DIST / "locus.svg")
