import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from taskfleet import Simulation, load_instance
from taskfleet.benchmark import run_benchmark
from taskfleet.instances import generate_instance
from taskfleet.metrics import metrics

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app, raise_server_exceptions=False)


def data(name="simple"):
    return load_instance(ROOT / "examples" / f"{name}.json").to_dict()


def deterministic(values):
    return {k: v for k, v in values.items() if k != "strategy_seconds"}


def test_examples_are_allowlisted_and_have_real_initial_snapshots():
    names = client.get("/api/examples").json()
    assert names == sorted(p.name for p in (ROOT / "examples").glob("*.json"))
    for name in names:
        response = client.get(f"/api/examples/{name}")
        assert response.status_code == 200
        initial = load_instance(ROOT / "examples" / name)
        assert response.json() == {"instance": initial.to_dict(), "snapshot": Simulation(initial).snapshot()}


@pytest.mark.parametrize("name", ["unknown.json", "app.py", "..%5Crequirements.txt", "%2e%2e%2frequirements.txt", "E:%5Cprivate.json"])
def test_example_paths_rejected(name):
    assert client.get(f"/api/examples/{name}").status_code == 404


@pytest.mark.parametrize("name", ["simple", "allocation", "contention", "dynamic", "complex_comparison"])
def test_validation_export_import_roundtrip(name):
    original = data(name)
    for value in (original, json.dumps(original)):
        response = client.post("/api/instances/validate", json={"instance": value})
        assert response.status_code == 200
        assert response.json()["instance"] == original


@pytest.mark.parametrize("value,pattern", [
    ('{"width": 1, "width": 2}', "duplicate field"),
    ('{"width":', "Invalid JSON"), ({}, "missing fields"),
    ({**data(), "width": True}, "integer"),
    ({**data(), "robots": [{"id": "r1", "position": [99, 0], "capacity": 1}]}, "outside grid"),
    ({**data(), "resources": [{"id": "station", "position": [0, 0], "capacity": 0}]}, "capacity"),
])
def test_invalid_instance_explicit_and_does_not_run(monkeypatch, value, pattern):
    def unexpected(*args):
        pytest.fail("Invalid instance executed")

    monkeypatch.setattr(Simulation, "run", unexpected)
    for endpoint in ("instances/validate", "simulations", "comparisons"):
        response = client.post(f"/api/{endpoint}", json={"instance": value})
        assert response.status_code == 422 and pattern in response.json()["detail"]


@pytest.mark.parametrize("strategy", ["fifo", "cpsat", "cnp"])
def test_api_matches_direct_engine_repeat_and_invariants(strategy):
    original = data("dynamic")
    before = copy.deepcopy(original)
    body = {"instance": original, "strategy": strategy, "solver_budget": 0.5}
    actual = client.post("/api/simulations", json=body)
    repeat = client.post("/api/simulations", json=body)
    assert actual.status_code == repeat.status_code == 200
    simulation = Simulation(load_instance(ROOT / "examples/dynamic.json"), strategy, solver_budget=0.5)
    simulation.run()
    simulation.check_invariants()
    assert actual.json()["trace"] == repeat.json()["trace"] == simulation.trace()
    assert deterministic(actual.json()["metrics"]) == deterministic(metrics(simulation))
    assert len(actual.json()["trace"]["snapshots"]) == original["horizon"] + 1
    assert original == before


def test_generation_matches_python_preserves_environment_and_is_reproducible():
    original = data("contention")
    body = dict(width=8, height=6, robot_count=3, task_count=10, seed=42, horizon=100,
                obstacles=original["obstacles"], resources=original["resources"])
    actual = client.post("/api/instances/generate", json=body)
    assert actual.status_code == 200
    assert actual.json() == client.post("/api/instances/generate", json=body).json()
    expected = generate_instance(**body)
    assert actual.json() == {"instance": expected.to_dict(), "snapshot": Simulation(expected).snapshot()}
    assert actual.json()["instance"]["unavailability"] == []


@pytest.mark.parametrize("change", [{"width": 0}, {"robot_count": 0}, {"seed": -1}, {"task_count": 1.5}, {"width": True}])
def test_bad_generation_parameters(change):
    body = dict(width=8, height=6, robot_count=3, task_count=10, seed=42)
    assert client.post("/api/instances/generate", json={**body, **change}).status_code == 422


@pytest.mark.parametrize("change", [{"strategy": "manual"}, {"strategy": "unknown"}, {"solver_budget": 0}, {"solver_budget": -1}, {"solver_budget": True}, {"solver_budget": "nan"}, {"solver_budget": "1"}, {"arbitrary_path": "private.json"}])
def test_bad_execution_parameters(change):
    assert client.post("/api/simulations", json={"instance": data(), **change}).status_code == 422


def test_malformed_http_json():
    response = client.post("/api/simulations", content='{"instance":', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_comparison_matches_m5_and_complex_reference():
    original = data("complex_comparison")
    response = client.post("/api/comparisons", json={"instance": original})
    assert response.status_code == 200
    report = response.json()
    expected = run_benchmark({"personnalisee": load_instance(ROOT / "examples/complex_comparison.json")}, controls=False)
    assert [deterministic(row) for row in report["runs"]] == [deterministic(row) for row in expected["runs"]]
    assert report["scenarios"]["personnalisee"] == original
    assert len({row["instance_sha256"] for row in report["runs"]}) == 1
    assert [tuple(row[key] for key in ("tasks_completed", "makespan", "total_tardiness", "distance", "messages", "invalid_decisions")) for row in report["runs"]] == [
        (18, 154, 437, 340, 0, 0), (18, 114, 179, 279, 0, 0), (18, 142, 365, 312, 1919, 0)]


@pytest.mark.parametrize("horizon", [0, 1])
def test_incomplete_no_fake_makespan(horizon):
    body = {"instance": {**data(), "horizon": horizon}}
    single = client.post("/api/simulations", json=body).json()
    assert single["metrics"]["makespan"] is None
    assert single["trace"]["current"]["tick"] == horizon
    assert all(row["status"] == "incomplete" and row["makespan"] is None
               for row in client.post("/api/comparisons", json=body).json()["runs"])


def test_strategy_failure_isolated_and_internal_error_hidden(monkeypatch):
    run = Simulation.run

    def failed(simulation, *args):
        if simulation.strategy == "cpsat":
            raise RuntimeError("private solver details")
        return run(simulation, *args)

    monkeypatch.setattr(Simulation, "run", failed)
    body = {"instance": data(), "strategy": "cpsat"}
    response = client.post("/api/simulations", json=body)
    assert response.status_code == 500 and "private" not in response.text
    report = client.post("/api/comparisons", json={"instance": data()}).json()
    assert [row["status"] for row in report["runs"]] == ["complete", "failed", "complete"]
    assert "private" not in json.dumps(report)
    assert "makespan" not in report["runs"][1]


def test_cors_only_local_vite():
    for origin, allowed in [("http://127.0.0.1:5173", True), ("http://localhost:5173", True), ("https://other.example", False)]:
        response = client.options("/api/simulations", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert (response.headers.get("access-control-allow-origin") == origin) == allowed
