"""Thin FastAPI adapter: scientific rules remain in taskfleet."""

import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.benchmark import run_benchmark
from taskfleet.instances import generate_instance, loads_instance
from taskfleet.metrics import metrics

from .experiments import router as experiments_router

logger = logging.getLogger(__name__)
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
app = FastAPI(title="TaskFleet", version="1.0")
app.include_router(experiments_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class InstanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Raw JSON preserves duplicate-key validation on imports.
    instance: dict | str


class ComparisonRequest(InstanceRequest):
    solver_budget: StrictFloat = Field(default=1.0, gt=0, allow_inf_nan=False)


class SimulationRequest(ComparisonRequest):
    strategy: Literal["fifo", "cpsat", "cnp"] = "fifo"


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: StrictInt = Field(ge=1)
    height: StrictInt = Field(ge=1)
    robot_count: StrictInt = Field(ge=0)
    task_count: StrictInt = Field(ge=0)
    seed: StrictInt = Field(ge=0)
    horizon: StrictInt = Field(default=100, ge=0)
    obstacles: list = Field(default_factory=list)
    resources: list = Field(default_factory=list)


def validated(value: dict | str):
    try:
        return loads_instance(value) if isinstance(value, str) else from_dict(value)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def preview(instance) -> dict:
    return {"instance": instance.to_dict(), "snapshot": Simulation(instance).snapshot()}


@app.exception_handler(RequestValidationError)
async def request_error(request: Request, error: RequestValidationError):
    detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def internal_error(request: Request, error: Exception):
    logger.error("TaskFleet request failed", exc_info=error)
    return JSONResponse(status_code=500, content={"detail": "Calcul impossible. Consulter le journal du serveur."})


@app.get("/api/examples")
def examples() -> list[str]:
    return sorted(p.name for p in EXAMPLES.glob("*.json") if p.is_file() and not p.is_symlink())


@app.get("/api/examples/{name}")
def example(name: str) -> dict:
    if name not in examples():
        raise HTTPException(404, "Exemple inconnu.")
    return preview(load_instance(EXAMPLES / name))


@app.post("/api/instances/validate")
def validate(body: InstanceRequest) -> dict:
    return preview(validated(body.instance))


@app.post("/api/instances/generate")
def generate(body: GenerationRequest) -> dict:
    try:
        instance = generate_instance(**body.model_dump())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return preview(instance)


@app.post("/api/simulations")
def simulate(body: SimulationRequest) -> dict:
    simulation = Simulation(validated(body.instance), body.strategy, solver_budget=body.solver_budget)
    simulation.run()
    simulation.check_invariants()
    return {"trace": simulation.trace(), "metrics": metrics(simulation)}


@app.post("/api/comparisons")
def compare(body: ComparisonRequest) -> dict:
    report = run_benchmark({"personnalisee": validated(body.instance)},
                           solver_budget=body.solver_budget, controls=False)
    for row in report["runs"]:
        if row["status"] == "failed":
            logger.error("%s failed: %s", row["strategy"], row["error"])
            row["error"] = "Calcul de cette stratégie impossible. Consulter le journal du serveur."
    return report
