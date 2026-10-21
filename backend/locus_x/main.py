from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .benchmark import run_benchmark
from .building import Building
from .environment import load_environments, resolve_floor
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
sessions: dict[str, SimulationSession] = {}
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
        session = SimulationSession(environment, request.settings, request.floor_id)
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


DIST = Path(__file__).resolve().parents[2] / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")

    @app.get("/locus.svg")
    def icon():
        return FileResponse(DIST / "locus.svg")
