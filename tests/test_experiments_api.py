"""The web adapters preserve scientific validation and keep slow work asynchronous."""

from copy import deepcopy
import json
from pathlib import Path
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from api import experiments
from api.main import app
from taskfleet import load_instance
from taskfleet import mapf


ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app, raise_server_exceptions=False)


def instance(name="allocation.json"):
    directory = "docs/results/v2" if name.startswith("m10-") else "examples"
    return load_instance(ROOT / directory / name).to_dict()


def launch(body):
    response = client.post("/api/v2/jobs", json=body)
    assert response.status_code == 202, response.text
    job = response.json()
    deadline = time.monotonic() + 20
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)
        response = client.get("/api/v2/jobs/" + job["id"])
        assert response.status_code == 200
        job = response.json()
    assert job["status"] not in {"queued", "running"}, job
    return job


def test_catalog_contains_real_instances_allowlisted_files_and_frozen_choices():
    response = client.get("/api/v2/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert {"m7.json", "m7_robust.json", "m10-test-0.json"} <= {i["id"] for i in catalog["warehouse_instances"]}
    complex_case = next(i for i in catalog["warehouse_instances"] if i["id"] == "complex_comparison.json")
    assert complex_case["milestones"] == ["m8"]
    for entry in catalog["warehouse_instances"]:
        assert entry["instance"] == instance(entry["id"])
    assert {entry["id"] for entry in catalog["checkpoints"] if entry["source"] == "historical"} == {
        f"m10-q-seed-{seed}.json" for seed in range(3)}
    assert catalog["selected_models"] == {"m8": "qwen2.5:7b-instruct-q4_K_M", "m10": "qwen3:4b-instruct-2507-q4_K_M"}
    assert all("instance" in item for item in catalog["mapf_instances"])
    assert catalog["limits"]["active_jobs"] == 4


@pytest.mark.parametrize("milestone,method", [("m7", "nominal"), ("m8", "dsa"),
                                               ("m9", "cbs"), ("m10", "heuristic")])
def test_compatible_catalog_uses_job_validation_and_accepts_current_user_instance(monkeypatch,
                                                                               milestone, method):
    monkeypatch.setattr(experiments._executor, "submit", lambda *args: pytest.fail("Catalog queued a job"))
    original = instance()
    body = {"milestone": milestone, "method": method, "instance": original}
    response = client.post("/api/v2/catalog", json=body)
    assert response.status_code == 200, response.text
    selected = response.json()
    entries = selected["mapf_instances"] if milestone == "m9" else selected["warehouse_instances"]
    assert entries
    assert ("user:current" in {row["id"] for row in entries}) == (milestone != "m9")
    for entry in entries:
        assert experiments._validate(experiments.ExperimentRequest(
            milestone=milestone, method=method, instance=entry["instance"]))
    assert original == instance()
    if milestone == "m9":
        assert any(row["id"] == "user:current" for row in selected["incompatible_instances"])
        assert not selected["warehouse_instances"]


def test_catalog_discovers_instances_by_format_inside_authorized_roots(monkeypatch, tmp_path):
    warehouse = tmp_path / "examples/nested/shared.json"
    paths = tmp_path / "docs/results/v2/nested/paths.json"
    warehouse.parent.mkdir(parents=True)
    paths.parent.mkdir(parents=True)
    warehouse.write_text(" " * 100_000 + json.dumps(instance()), encoding="utf-8")
    problem = mapf.load_problem(ROOT / "examples/mapf/crossing.json").to_dict()
    paths.write_text(json.dumps(problem), encoding="utf-8")
    monkeypatch.setattr(experiments, "ROOT", tmp_path)
    catalog = client.get("/api/v2/catalog").json()
    assert catalog["warehouse_instances"][0]["id"] == "shared.json"
    assert catalog["warehouse_instances"][0]["milestones"] == ["m7", "m8", "m10"]
    assert catalog["mapf_instances"] == [{"id": "paths.json", "label": "paths.json", "instance": problem}]


def test_catalog_keeps_valid_infeasible_instances_and_explains_robust_or_checkpoint_mismatch():
    tiny_horizon = instance("m7.json") | {"horizon": 1}
    catalog = client.post("/api/v2/catalog", json={
        "milestone": "m7", "method": "nominal", "instance": tiny_horizon}).json()
    assert "user:current" in {row["id"] for row in catalog["warehouse_instances"]}
    for parameters, reason in [
        ({"milestone": "m7", "method": "robust", "scenario_set": "absent.json"}, "scenario"),
        ({"milestone": "m10", "method": "q", "checkpoint": "absent.json"}, "checkpoint"),
    ]:
        catalog = client.post("/api/v2/catalog", json=parameters | {"instance": instance()}).json()
        assert not catalog["warehouse_instances"]
        entry = next(row for row in catalog["incompatible_instances"] if row["id"] == "user:current")
        assert reason in entry["reason"].lower()
    catalog = client.post("/api/v2/catalog", json={
        "milestone": "m7", "method": "robust", "instance": instance(),
        "scenario_set": "robust_scenarios.json"}).json()
    assert catalog["warehouse_instances"]
    assert "user:current" not in {row["id"] for row in catalog["warehouse_instances"]}
    assert any(row["id"] == "user:current" and "robot" in row["reason"].lower()
               for row in catalog["incompatible_instances"])


@pytest.mark.parametrize("milestone,method", [("m7", "nominal"), ("m7", "rolling"), ("m8", "dsa"),
                                               ("m10", "random"), ("m10", "heuristic"), ("m10", "q")])
def test_warehouse_methods_return_checked_replay_and_real_metrics(milestone, method):
    original = instance()
    body = {"milestone": milestone, "method": method, "instance": original}
    if method == "q":
        body["checkpoint"] = "m10-q-seed-0.json"
    job = launch(body)
    assert job["status"] == "completed", job
    result = job["result"]
    assert result["milestone"] == milestone and result["method"] == method
    assert result["execution_status"] == "valid"
    assert result["metrics"]["tasks_completed"] == 2
    trace = result["trace"]
    assert trace["instance"] == original
    assert trace["snapshots"][0]["tick"] == 0
    assert trace["current"]["tick"] <= original["horizon"]
    assert original == instance()
    if milestone == "m7" and method == "nominal":
        assert result["execution"]["executable"] and not result["execution"]["rejected_dispatches"]
    if milestone == "m10":
        assert result["episode"]["trace"] == trace
        assert result["episode"]["return"] == sum(result["episode"]["rewards"])
        if method == "q":
            assert result["checkpoint"] == body["checkpoint"]


def test_dcop_parameters_history_comparison_and_confirmation_before_engine():
    body = {"milestone": "m8", "method": "dsa", "instance": instance(), "rounds": 3,
            "probability": 0.25, "seed": 19, "message_budget": 100, "solver_budget": 0.1, "compare": True}
    result = launch(body)["result"]
    coordination = result["coordination"]
    assert coordination["round_budget"] == 3 and coordination["probability"] == 0.25
    assert coordination["seed"] == 19 and coordination["message_budget"] == 100
    assert coordination["final_quality"]["feasible"]
    assert len({task for _, task in coordination["assignments"]}) == len(coordination["assignments"])
    assert {"domain", "claim", "confirmed"} <= {m["type"] for m in coordination["messages"]}
    assert coordination["history"]
    assert {"fifo", "cpsat", "cnp", "dsa"} <= {row["method"] for row in result["comparison"]}
    assert all(row["metrics"]["invalid_decisions"] == 0 for row in result["comparison"])


def test_dcop_duplicate_proposals_are_confirmed_without_double_assignment():
    data = {"width": 1, "height": 1, "horizon": 4,
            "robots": [{"id": r, "position": [0, 0], "capacity": 1} for r in ("a", "b")],
            "tasks": [{"id": "task", "pickup": [0, 0], "destination": [0, 0]}]}
    result = launch({"milestone": "m8", "method": "dsa", "instance": data, "rounds": 0})["result"]
    assert result["coordination"]["raw_quality"]["conflict_pairs"] == 1
    assert result["coordination"]["assignments"] == [["a", "task"]]
    assert result["metrics"]["invalid_decisions"] == 0
    assert result["coordination"]["confirmation_abandonments"] == 1


def test_dcop_budget_exhaustion_is_not_a_technical_failure():
    job = launch({"milestone": "m8", "method": "dsa", "instance": instance(),
                  "message_budget": 0, "compare": True})
    assert job["status"] == "completed"
    assert job["result"]["execution_status"] == "budget_exhausted"
    assert job["result"]["coordination"]["assignments"] == []
    dsa = next(row for row in job["result"]["comparison"] if row["method"] == "dsa")
    assert dsa["execution_status"] == "budget_exhausted"


@pytest.mark.parametrize("method", ["prioritized", "cbs"])
def test_mapf_methods_comparison_replay_and_independent_validation(method):
    data = mapf.load_problem(ROOT / "examples/mapf/crossing.json").to_dict()
    job = launch({"milestone": "m9", "method": method, "instance": data, "compare": True,
                  "priorities": ["b", "a"], "horizon": 8, "max_nodes": 5000, "seconds": 1.0})
    result = job["result"]
    assert job["status"] == "completed" and result["execution_status"] == "valid"
    assert "trace" not in result
    assert result["problem"]["horizon"] == 8
    assert result["plan"]["validation"]["valid"]
    assert mapf.validate_plan(mapf.from_dict(result["problem"]), result["plan"]["paths"])["valid"]
    assert {row["method"] for row in result["comparison"]} == {"prioritized", "cbs"}
    assert result["plan"]["budgets"] == {"max_nodes": 5000, "seconds": 1.0}


def test_mapf_budget_exhaustion_never_returns_an_unchecked_plan():
    data = mapf.load_problem(ROOT / "examples/mapf/crossing.json").to_dict()
    result = launch({"milestone": "m9", "method": "cbs", "instance": data, "max_nodes": 1})["result"]
    assert result["execution_status"] == "budget_exhausted"
    assert result["plan"]["paths"] is None and result["metrics"] == {}


def test_training_selects_on_validation_and_reuses_in_memory_frozen_checkpoint():
    files = experiments._checkpoint_files()
    before = {name: path.read_bytes() for name, path in files.items()}
    body = {"milestone": "m10", "method": "train", "instance": instance("m10-test-0.json"),
            "episodes": 2, "checkpoint_every": 1, "alpha": 0.3, "seed": 5,
            "training_instances": ["m10-train-0.json"], "validation_instances": ["m10-validation-0.json"]}
    job = launch(body)
    assert job["status"] == "completed", job
    result = job["result"]
    assert result["training"]["episodes"] == 2 and result["training"]["alpha"] == 0.3
    assert len(result["training"]["curve"]) == len(result["training"]["validation"]) == 2
    checkpoint = result["checkpoint"]
    assert checkpoint.startswith("session:")
    assert checkpoint in {entry["id"] for entry in client.get("/api/v2/catalog").json()["checkpoints"]}
    frozen = deepcopy(experiments._get_policy(checkpoint).to_dict())
    evaluated = launch({"milestone": "m10", "method": "q", "instance": instance("m10-test-0.json"),
                        "checkpoint": checkpoint, "seed": 5})
    assert evaluated["status"] == "completed"
    assert evaluated["result"]["episode"]["return"] == result["episode"]["return"]
    assert experiments._get_policy(checkpoint).to_dict() == frozen
    assert all(path.read_bytes() == before[name] for name, path in files.items())


@pytest.mark.parametrize("change", [
    {"milestone": "m11"}, {"method": "oracle"}, {"rounds": True}, {"rounds": 101},
    {"probability": "0.7"}, {"probability": 1.1}, {"message_budget": -1}, {"seed": -1},
    {"solver_budget": 0}, {"solver_budget": 0.6}, {"horizon": 301}, {"url": "http://example.com"},
    {"checkpoint": "../../private.json"}, {"model": "test"}, {"window": 5},
])
def test_invalid_or_irrelevant_parameters_are_rejected_before_queue(monkeypatch, change):
    monkeypatch.setattr(experiments._executor, "submit", lambda *args: pytest.fail("Invalid request queued"))
    body = {"milestone": "m8", "method": "dsa", "instance": instance()} | change
    assert client.post("/api/v2/jobs", json=body).status_code == 422


@pytest.mark.parametrize("body", [
    {"milestone": "m7", "method": "nominal", "instance": instance("complex_comparison.json")},
    {"milestone": "m7", "method": "nominal", "instance": instance() | {"horizon": 60}, "compare": True, "solver_budget": 0.5},
    {"milestone": "m7", "method": "robust", "instance": instance("m7.json"), "scenario_set": "../../private.json"},
    {"milestone": "m8", "method": "dsa", "instance": instance() | {"width": 1_000_000}},
    {"milestone": "m10", "method": "q", "instance": instance(), "checkpoint": "../../private.json"},
    {"milestone": "m10", "method": "q", "instance": instance()},
    {"milestone": "m10", "method": "heuristic", "instance": instance("complex_comparison.json")},
    {"milestone": "m10", "method": "train", "instance": instance(), "training_instances": ["../private.json"]},
    {"milestone": "m10", "method": "train", "instance": instance("m10-train-0.json")},
])
def test_invalid_instances_paths_and_training_splits_are_not_queued(monkeypatch, body):
    monkeypatch.setattr(experiments._executor, "submit", lambda *args: pytest.fail("Invalid request queued"))
    assert client.post("/api/v2/jobs", json=body).status_code == 422


def test_jobs_are_nonblocking_bounded_and_isolate_technical_errors(monkeypatch):
    entered, release = Event(), Event()
    original = experiments._run_m8

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(experiments, "_run_m8", slow)
    body = {"milestone": "m8", "method": "dsa", "instance": instance()}
    jobs = []
    try:
        first = client.post("/api/v2/jobs", json=body)
        assert first.status_code == 202 and entered.wait(2)
        jobs.append(first.json())
        polled = client.get("/api/v2/jobs/" + jobs[0]["id"]).json()
        assert polled["status"] == "running" and polled["result"] is None
        for _ in range(3):
            response = client.post("/api/v2/jobs", json=body)
            assert response.status_code == 202
            jobs.append(response.json())
        assert client.post("/api/v2/jobs", json=body).status_code == 429
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while any(client.get("/api/v2/jobs/" + job["id"]).json()["status"] in {"queued", "running"}
                  for job in jobs) and time.monotonic() < deadline:
            time.sleep(0.01)
    assert client.get("/api/v2/jobs/unknown").status_code == 404

    def failed(*args):
        raise RuntimeError("private solver details")

    monkeypatch.setattr(experiments, "_run_m8", failed)
    job = launch(body)
    assert job["status"] == "failed" and job["error"]["kind"] == "technical_error"
    assert "private" not in str(job)


def test_completed_job_retention_is_bounded_and_expiration_is_explicit():
    with experiments._lock:
        saved = deepcopy(experiments._jobs)
        try:
            experiments._jobs.clear()
            for index in range(experiments.LIMITS["retained_jobs"]):
                experiments._jobs[str(index)] = {"status": "completed", "updated_at": time.time()}
            experiments._prune()
            assert len(experiments._jobs) == experiments.LIMITS["retained_jobs"] - 1
            experiments._jobs["expired"] = {"status": "completed", "updated_at": time.time() - 3601}
        finally:
            expired = experiments._jobs.get("expired")
            experiments._jobs.clear()
            experiments._jobs.update(saved)
            experiments._jobs["expired"] = expired
    try:
        assert client.get("/api/v2/jobs/expired").status_code == 404
    finally:
        with experiments._lock:
            experiments._jobs.pop("expired", None)
