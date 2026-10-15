"""Bounded asynchronous adapters for the existing V2 scientific functions."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from threading import Lock
from time import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from taskfleet import Simulation, from_dict, load_instance
from taskfleet import dcop, mapf, marl, ollama
from taskfleet.metrics import metrics
from taskfleet.ollama_selection import resolve_config

from .experiment_planning import run_m7, run_m9


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "docs/results/v2/ollama-selection-20261015/report.json"
OLLAMA_URL = "http://127.0.0.1:11434"
router = APIRouter(prefix="/api/v2")
logger = logging.getLogger(__name__)
LIMITS = {
    "horizon": 300, "m7_horizon": 60, "m10_horizon": 200,
    "solver_budget": 0.5, "rolling_total_budget": 12.0, "window": 60,
    "frequency": 60, "max_policies": 500, "rounds": 100,
    "message_budget": 100_000, "max_nodes": 100_000, "seconds": 10.0,
    "episodes": 30, "checkpoint_every": 30, "call_budget": 16,
    "timeout": 30.0, "load_timeout": 180.0, "scenario_budget": 180.0,
    "max_tokens": 256, "active_jobs": 4, "retained_jobs": 24,
    "retention_seconds": 3600,
}
# ponytail: a bounded process-local queue; restart discards jobs and session policies.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="taskfleet-v2")
_lock = Lock()
_jobs = OrderedDict()
_policies = OrderedDict()


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    milestone: Literal["m7", "m8", "m9", "m10"]
    method: str = Field(min_length=1, max_length=20)
    instance: dict
    compare: StrictBool = False
    horizon: StrictInt | None = Field(default=None, ge=0, le=300)
    solver_budget: StrictFloat = Field(default=0.2, gt=0, le=0.5, allow_inf_nan=False)
    window: StrictInt = Field(default=18, ge=1, le=60)
    frequency: StrictInt = Field(default=3, ge=1, le=60)
    scenario_set: str | None = Field(default=None, max_length=100)
    max_policies: StrictInt = Field(default=200, ge=1, le=500)
    rounds: StrictInt = Field(default=20, ge=0, le=100)
    probability: StrictFloat = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    seed: StrictInt = Field(default=0, ge=0, le=2_147_483_647)
    message_budget: StrictInt = Field(default=10_000, ge=0, le=100_000)
    priorities: list[str] | None = Field(default=None, max_length=8)
    max_nodes: StrictInt = Field(default=10_000, ge=1, le=100_000)
    seconds: StrictFloat = Field(default=2.0, gt=0, le=10, allow_inf_nan=False)
    checkpoint: str | None = Field(default=None, max_length=100)
    episodes: StrictInt = Field(default=10, ge=1, le=30)
    checkpoint_every: StrictInt = Field(default=5, ge=1, le=30)
    alpha: StrictFloat = Field(default=0.2, gt=0, le=1, allow_inf_nan=False)
    training_instances: list[str] | None = Field(default=None, min_length=1, max_length=4)
    validation_instances: list[str] | None = Field(default=None, min_length=1, max_length=2)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    preload: StrictBool = True
    timeout: StrictFloat = Field(default=30.0, gt=0, le=30, allow_inf_nan=False)
    load_timeout: StrictFloat = Field(default=120.0, gt=0, le=180, allow_inf_nan=False)
    call_budget: StrictInt = Field(default=8, ge=0, le=16)
    scenario_budget: StrictFloat = Field(default=120.0, gt=0, le=180, allow_inf_nan=False)
    max_tokens: StrictInt = Field(default=64, ge=1, le=256)


class CatalogRequest(ExperimentRequest):
    instance: dict | None = None


METHODS = {"m7": {"nominal", "rolling", "robust"}, "m8": {"dsa", "llm"},
           "m9": {"prioritized", "cbs"}, "m10": {"random", "heuristic", "q", "llm", "train"}}
COMMON_FIELDS = {"milestone", "method", "instance", "compare", "horizon"}
LLM_FIELDS = {"model", "preload", "timeout", "load_timeout", "call_budget", "scenario_budget", "max_tokens"}


def _files(directory, pattern):
    return {p.name: p for p in sorted(directory.glob(pattern)) if p.is_file() and not p.is_symlink()}


def _warehouse_files():
    # Only these repository roots are discoverable; imported instances arrive as JSON.
    instances = {}
    for directory in (ROOT / "examples", ROOT / "docs/results/v2"):
        for path in sorted(directory.rglob("*.json")):
            if (not path.is_file() or path.is_symlink()
                    or not path.resolve().is_relative_to(directory.resolve())):
                continue
            identifier = path.name if path.name not in instances else path.relative_to(ROOT).as_posix()
            instances[identifier] = path
    return instances


def _training_files():
    return _files(ROOT / "docs/results/v2", "m10-train-*.json")


def _validation_files():
    return _files(ROOT / "docs/results/v2", "m10-validation-*.json")


def _checkpoint_files():
    return _files(ROOT / "docs/results/v2", "m10-q-seed-*.json")


def _scenario_files():
    return _files(ROOT / "examples/m7", "*scenarios.json")


@lru_cache(maxsize=1)
def _selection():
    return json.loads(SELECTION.read_text(encoding="utf-8"))["selection"]


def _selected_models():
    return {usage: _selection()[usage]["model"] for usage in ("m8", "m10")}


def _bounded_instance(data, milestone):
    # Reject large grids before the scientific parser performs BFS validation.
    if len(json.dumps(data)) > 100_000:
        raise ValueError("Instance exceeds the 100 KB web limit")
    for name in ("width", "height"):
        if type(data.get(name)) is not int or not 1 <= data[name] <= 20:
            raise ValueError(f"{name} must be an integer in [1, 20]")
    arrays = {"robots": 8, "tasks": 24, "obstacles": 400, "resources": 30, "unavailability": 50}
    for name, maximum in arrays.items():
        value = data.get(name, [])
        if not isinstance(value, list) or len(value) > maximum:
            raise ValueError(f"{name} exceeds the web limit of {maximum}")
        if name in {"robots", "tasks", "resources"}:
            if any(not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                   or len(entry["id"]) > 100 for entry in value):
                raise ValueError(f"{name}: IDs must be strings of at most 100 characters")
        for entry in value:
            if isinstance(entry, dict):
                for field in ("capacity", "load", "deadline", "arrival_tick", "start_tick", "end_tick"):
                    number = entry.get(field)
                    if type(number) is int and not 0 <= number <= 1_000_000:
                        raise ValueError(f"{name}.{field}: web values must be in [0, 1000000]")
    if type(data.get("seed")) is int and not 0 <= data["seed"] <= 2_147_483_647:
        raise ValueError("seed exceeds the web integer limit")
    if type(data.get("horizon", 100)) is not int or not 0 <= data.get("horizon", 100) <= 300:
        raise ValueError("horizon must be an integer in [0, 300]")
    if any(type(t.get("handling_ticks", 1)) is not int or not 1 <= t.get("handling_ticks", 1) <= 20
           for t in data.get("tasks", [])):
        raise ValueError("handling_ticks must be in [1, 20]")
    if milestone == "m7" and (data["width"] > 6 or data["height"] > 6
            or len(data.get("robots", [])) > 3 or len(data.get("tasks", [])) > 5
            or data.get("horizon", 100) > 60
            or any(t.get("handling_ticks", 1) > 5 for t in data.get("tasks", []))):
        raise ValueError("Scheduling web limit: grid 6x6, 3 robots, 5 tasks, horizon 60, handling 5")
    instance = from_dict(data)
    if milestone == "m10":
        if instance.horizon > 200:
            raise ValueError("Cooperative learning web horizon is limited to 200 ticks")
        marl.AllocationEnv(instance)
    return instance


def _physical(instance):
    return json.dumps({k: v for k, v in instance.to_dict().items() if k != "seed"}, sort_keys=True)


def _get_policy(identifier):
    files = _checkpoint_files()
    if identifier in files:
        return marl.QPolicy.load(files[identifier])
    with _lock:
        if identifier in _policies:
            return marl.QPolicy(_policies[identifier].tables)
    raise ValueError("Unknown checkpoint; choose an identifier from the catalog")


def _validate(body):
    if body.method not in METHODS[body.milestone]:
        raise ValueError(f"Unknown {body.milestone} method")
    allowed = set(COMMON_FIELDS)
    if body.milestone == "m7":
        allowed |= {"solver_budget"}
        if body.method == "rolling":
            allowed |= {"window", "frequency"}
        if body.method == "robust":
            allowed |= {"scenario_set", "max_policies"}
    if body.milestone == "m8":
        allowed |= {"rounds", "probability", "seed", "message_budget", "solver_budget"}
    if body.milestone == "m9":
        allowed |= {"max_nodes", "seconds"}
        if body.method == "prioritized" or body.compare:
            allowed.add("priorities")
    if body.milestone == "m10":
        allowed.add("seed")
        if body.method == "q":
            allowed.add("checkpoint")
        if body.method == "train":
            allowed |= {"episodes", "checkpoint_every", "alpha", "training_instances", "validation_instances"}
            if body.compare:
                raise ValueError("Training and frozen-policy comparison are separate operations")
    if body.method == "llm":
        allowed |= LLM_FIELDS
    unexpected = body.model_fields_set - allowed
    if unexpected:
        raise ValueError(f"Parameters unavailable for this method: {sorted(unexpected)}")
    if body.model is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", body.model):
        raise ValueError("Invalid Ollama model identifier")
    data = deepcopy(body.instance)
    if body.horizon is not None:
        data["horizon"] = body.horizon
    prepared = {}
    if body.milestone == "m9":
        if len(json.dumps(data)) > 50_000 or not isinstance(data.get("agents"), list) or len(data["agents"]) > 8:
            raise ValueError("MAPF web limit: 8 agents and 50 KB")
        if any(type(data.get(k)) is not int or not 1 <= data[k] <= 20 for k in ("width", "height")):
            raise ValueError("MAPF web grid limit is 20x20")
        if type(data.get("horizon")) is not int or not 0 <= data["horizon"] <= 100:
            raise ValueError("MAPF web horizon must be in [0, 100]")
        instance = mapf.from_dict(data)
        if body.priorities is not None and (len(body.priorities) != len(instance.starts)
                or set(body.priorities) != set(instance.starts)):
            raise ValueError("priorities must contain every agent exactly once")
    else:
        instance = _bounded_instance(data, body.milestone)
    prepared["instance"] = instance
    if body.milestone == "m7":
        if (body.method == "rolling" or body.method == "nominal" and body.compare) and instance.horizon * body.solver_budget > 12:
            raise ValueError("Rolling horizon * solver_budget must not exceed 12 deterministic seconds per phase")
        if body.method == "robust":
            identifier = body.scenario_set or "scenarios.json"
            files = _scenario_files()
            if identifier not in files:
                raise ValueError("Unknown robust scenario set")
            scenarios = json.loads(files[identifier].read_text(encoding="utf-8"))
            if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 6:
                raise ValueError("Robust web limit: 1 to 6 scenarios")
            for scenario in scenarios:
                _bounded_instance(instance.to_dict() | {"unavailability": scenario}, "m7")
            prepared["scenarios"] = scenarios
    if body.milestone == "m10" and body.method == "q":
        prepared["policy"] = _get_policy(body.checkpoint)
    if body.milestone == "m10" and body.method == "train":
        for label, identifiers, files in (
            ("training", body.training_instances, _training_files()),
            ("validation", body.validation_instances, _validation_files()),
        ):
            identifiers = list(files) if identifiers is None else identifiers
            if len(identifiers) != len(set(identifiers)) or any(i not in files for i in identifiers):
                raise ValueError(f"Unknown or duplicate {label} instance identifier")
            prepared[label] = [_bounded_instance(load_instance(files[i]).to_dict(), "m10") for i in identifiers]
        training = {_physical(i) for i in prepared["training"]}
        validation = {_physical(i) for i in prepared["validation"]}
        if training & validation:
            raise ValueError("Training and validation instances must be physically disjoint")
        if _physical(instance) in training | validation:
            raise ValueError("Choose a separate evaluation instance for the frozen trained policy")
    return prepared


def _prune():
    for identifier, job in list(_jobs.items()):
        if job["status"] in {"completed", "failed"} and time() - job["updated_at"] > 3600:
            del _jobs[identifier]
    while len(_jobs) >= LIMITS["retained_jobs"]:
        oldest = next((key for key, job in _jobs.items() if job["status"] in {"completed", "failed"}), None)
        if oldest is None:
            break
        del _jobs[oldest]


@router.get("/catalog")
def catalog():
    warehouse, paths = [], []
    for identifier, path in _warehouse_files().items():
        try:
            data = load_instance(path).to_dict()
        except ValueError:
            try:
                problem = mapf.load_problem(path).to_dict()
                paths.append({"id": identifier, "label": identifier, "instance": problem})
            except ValueError:
                pass
            continue
        supported = []
        for milestone in ("m7", "m8", "m10"):
            try:
                _bounded_instance(data, milestone)
                supported.append(milestone)
            except ValueError:
                pass
        warehouse.append({"id": identifier, "label": identifier, "instance": data, "milestones": supported})
    with _lock:
        checkpoints = [{"id": name, "label": name, "source": "session"} for name in _policies]
    return {"warehouse_instances": warehouse,
            "mapf_instances": paths,
            "scenario_sets": [{"id": name, "label": name, "scenarios": json.loads(path.read_text(encoding="utf-8"))}
                              for name, path in _scenario_files().items()],
            "checkpoints": [{"id": name, "label": name, "source": "historical"} for name in _checkpoint_files()] + checkpoints,
            "training_sets": [{"id": name, "label": name} for name in _training_files()],
            "validation_sets": [{"id": name, "label": name} for name in _validation_files()],
            "selected_models": _selected_models(), "limits": LIMITS}


@router.post("/catalog")
def compatible_catalog(body: CatalogRequest):
    """Use the job validator for selection, without invoking any scientific solver."""
    result = catalog()
    if body.instance is not None:
        kind = "mapf_instances" if "agents" in body.instance else "warehouse_instances"
        result[kind].insert(0, {"id": "user:current", "label": "Instance utilisateur",
                                "instance": body.instance})
    parameters = body.model_dump(exclude_unset=True, exclude={"instance"})
    incompatible = []
    for key, kind in (("warehouse_instances", "warehouse"), ("mapf_instances", "mapf")):
        compatible = []
        for entry in result[key]:
            try:
                _validate(ExperimentRequest(**parameters, instance=entry["instance"]))
                compatible.append(entry)
            except ValueError as error:
                incompatible.append({"id": entry["id"], "label": entry["label"],
                                     "format": kind, "reason": str(error)})
        result[key] = compatible
    return result | {"incompatible_instances": incompatible}


@router.get("/ollama")
def ollama_status():
    runtime = ollama.inspect_runtime(OLLAMA_URL, timeout=2.0)
    result = {**runtime, "available": runtime["version"] is not None,
              "models": [], "selected_models": _selected_models()}
    try:
        entries = ollama._request(OLLAMA_URL + "/api/tags", 2.0).get("models")
        if not isinstance(entries, list):
            raise ValueError("Invalid Ollama model inventory")
        result["models"] = [{"name": e["name"], "digest": e.get("digest"), "size_bytes": e.get("size")}
                            for e in entries[:64] if isinstance(e, dict) and isinstance(e.get("name"), str)]
        for usage, choice in _selection().items():
            if usage in ("m8", "m10"):
                installed = next((e for e in result["models"] if e["name"] == choice["model"]), None)
                if installed is None or installed["digest"] != choice["model_digest"]:
                    result["errors"].append({"usage": usage, "error": "model_not_found" if installed is None else "digest_mismatch"})
    except (OSError, ValueError) as error:
        result["errors"].append({"endpoint": "/api/tags", "error": str(error)})
    return result


def _update(identifier, **values):
    with _lock:
        _jobs[identifier].update(values, updated_at=time())


def _llm_client(identifier, body):
    _update(identifier, phase="verifying_model", message="Vérification du modèle installé et du digest sélectionné.")
    config = resolve_config(body.milestone, selection=SELECTION,
                            model=body.model, url=OLLAMA_URL, timeout=body.timeout,
                            load_timeout=body.load_timeout, call_budget=body.call_budget,
                            scenario_budget=body.scenario_budget, max_tokens=body.max_tokens, seed=body.seed)
    shown = ollama._request(OLLAMA_URL + "/api/show", 5.0, {"model": config.model, "verbose": False})
    capabilities = shown.get("capabilities")
    if shown.get("error") or isinstance(capabilities, list) and capabilities and "completion" not in capabilities:
        raise ValueError("Ollama model is incompatible with text generation")
    preparation = None
    if body.preload:
        _update(identifier, phase="loading_model", message="Chargement Ollama en cours ; budget distinct des décisions.")
        preparation = ollama.preload_model(config)
        if preparation["status"] != "ok":
            raise ValueError(f"Ollama preload failed: {preparation['error']}")
    _update(identifier, phase="executing", message="Décisions locales Ollama en cours.")
    return ollama.OllamaClient(config), preparation


def _execute_assignments(instance, assignments):
    simulation = Simulation(instance)
    checked = dcop.assignment_quality(simulation.observation(), assignments)
    if not checked["feasible"]:
        raise RuntimeError("Unconfirmed allocation rejected before execution")
    for robot, task in assignments:
        simulation.assign(robot, task)
    simulation.run()
    simulation.check_invariants()
    return simulation


def _run_m8(body, instance, client):
    observation = Simulation(instance).observation()
    arguments = dict(rounds=body.rounds, probability=body.probability,
                     seed=body.seed, message_budget=body.message_budget)
    comparison = None
    if body.compare:
        report = dcop.compare_snapshot(observation, **arguments, solver_budget=body.solver_budget,
                                       enumeration_budget=1000, llm=client)
        coordination = report["methods"][body.method]
        comparison = []
        for method, row in report["methods"].items():
            simulation = _execute_assignments(instance, row["assignments"])
            state = "valid" if simulation.all_tasks_completed else "incomplete"
            if row.get("stop_reason") == "COMMUNICATION_BUDGET":
                state = "budget_exhausted"
            comparison.append({"method": method, "execution_status": state,
                               "metrics": metrics(simulation) | row["quality"], "coordination": row})
    else:
        coordination = dcop.distributed(observation, **arguments, llm=client)
    simulation = _execute_assignments(instance, coordination["assignments"])
    state = "valid" if simulation.all_tasks_completed else "incomplete"
    if coordination["stop_reason"] == "COMMUNICATION_BUDGET":
        state = "budget_exhausted"
    return {"milestone": "m8", "method": body.method, "execution_status": state,
            "information": "One frozen observation; only confirmed assignments execute, without later reassignment.",
            "metrics": metrics(simulation) | coordination["final_quality"],
            "coordination": coordination, "trace": simulation.trace(), "comparison": comparison}


def _episode_metrics(episode):
    return {key: value for key, value in episode.items() if key not in {"trace", "decisions", "rewards", "llm_trace"}}


def _run_m10(identifier, body, prepared, client):
    instance = prepared["instance"]
    training = None
    checkpoint = body.checkpoint if body.method == "q" else None
    policy = prepared.get("policy")
    kind = body.method
    if kind == "train":
        _update(identifier, phase="training", message="Entraînement tabulaire court ; sélection sur validation uniquement.")
        policy, training = marl.train(prepared["training"], prepared["validation"], episodes=body.episodes,
                                      seed=body.seed, checkpoint_every=body.checkpoint_every, alpha=body.alpha)
        checkpoint = "session:" + identifier
        with _lock:
            while len(_policies) >= 8:
                _policies.popitem(last=False)
            _policies[checkpoint] = marl.QPolicy(policy.tables)
        training["training_instances"] = body.training_instances or list(_training_files())
        training["validation_instances"] = body.validation_instances or list(_validation_files())
        kind = "q"
        _update(identifier, phase="evaluating", message="Évaluation de la politique figée sur une instance séparée.")
    episode = marl.run_episode(instance, kind, policy=policy, seed=body.seed, client=client, trace=True)
    comparison = None
    if body.compare:
        comparison = []
        for method in dict.fromkeys(["random", "heuristic", kind]):
            row = episode if method == kind else marl.run_episode(instance, method, seed=body.seed)
            comparison.append({"method": method, "execution_status": "valid" if row["terminated"] else "incomplete",
                               "metrics": _episode_metrics(row)})
    return {"milestone": "m10", "method": body.method,
            "execution_status": "valid" if episode["terminated"] else "incomplete",
            "information": "Frozen policy evaluation; Ollama supplies local decisions and is not trained by reinforcement learning.",
            "metrics": _episode_metrics(episode), "trace": episode["trace"], "episode": episode,
            "training": training, "checkpoint": checkpoint, "comparison": comparison}


def _work(identifier, body, prepared):
    _update(identifier, status="running", phase="executing", message="Expérience scientifique en cours.")
    client = preparation = None
    try:
        if body.method == "llm":
            try:
                client, preparation = _llm_client(identifier, body)
            except (ValueError, OSError) as error:
                message = str(error)
                _update(identifier, status="failed", phase="failed", message=message,
                        error={"kind": "ollama_error", "message": message})
                return
        if body.milestone == "m7":
            result = run_m7(body, prepared["instance"], prepared.get("scenarios"))
        elif body.milestone == "m8":
            result = _run_m8(body, prepared["instance"], client)
        elif body.milestone == "m9":
            result = run_m9(body, prepared["instance"])
        else:
            result = _run_m10(identifier, body, prepared, client)
        if client is not None:
            result["ollama"] = {"model": client.config.model, "config": asdict(client.config),
                                "preload": preparation, "metrics": deepcopy(client.metrics), "trace": deepcopy(client.trace)}
            if client.metrics["errors"]:
                result["execution_status"] = "budget_exhausted" if any(
                    "budget" in key for key in client.metrics["errors"]) else "ollama_error"
                for row in result.get("comparison") or []:
                    if row["method"] == "llm":
                        row["execution_status"] = result["execution_status"]
        _update(identifier, status="completed", phase="completed", message="Calcul terminé ; consulter le statut scientifique.", result=result)
    except Exception as error:
        logger.exception("V2 experiment %s failed", identifier)
        message = str(error) if isinstance(error, ValueError) else "Calcul impossible. Consulter le journal du serveur."
        _update(identifier, status="failed", phase="failed", message=message,
                error={"kind": "technical_error", "message": message})


@router.post("/jobs", status_code=202)
def create_job(body: ExperimentRequest):
    try:
        prepared = _validate(body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    identifier = uuid4().hex
    job = {"id": identifier, "milestone": body.milestone, "method": body.method,
           "status": "queued", "phase": "queued", "message": "Expérience en attente.",
           "result": None, "error": None, "created_at": time(), "updated_at": time()}
    with _lock:
        _prune()
        if sum(j["status"] in {"queued", "running"} for j in _jobs.values()) >= LIMITS["active_jobs"]:
            raise HTTPException(429, "Quatre expériences actives au maximum ; attendre une fin de calcul.")
        _jobs[identifier] = job
        response = deepcopy(job)
    _executor.submit(_work, identifier, body, prepared)
    return response


@router.get("/jobs/{identifier}")
def get_job(identifier: str):
    with _lock:
        job = _jobs.get(identifier)
        if job is None or job["status"] in {"completed", "failed"} and time() - job["updated_at"] > 3600:
            raise HTTPException(404, "Expérience inconnue ou expirée.")
        return deepcopy(job)
