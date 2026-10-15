"""Ollama web jobs use the real client with a fake local HTTP transport."""

from itertools import count
import json
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from api import experiments as web
from api.main import app
from taskfleet import load_instance, ollama, ollama_selection


client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    selected = {
        usage: {"model": f"selected:{usage}", "model_digest": f"digest-{usage}",
                "config": {"model": f"selected:{usage}", "think": False}}
        for usage in ("m8", "m10")
    }
    report = tmp_path / "selection.json"
    report.write_text(json.dumps({"schema": 1, "selection": selected}), encoding="utf-8")
    monkeypatch.setattr(web, "SELECTION", report)
    monkeypatch.setattr(web, "_selection", lambda: selected)
    state = {"models": [{"name": row["model"], "digest": row["model_digest"]}
                        for row in selected.values()],
             "capabilities": ["completion"], "calls": [], "response": None,
             "failure": None, "load_failure": None, "load_started": Event(),
             "release_load": None}

    def request(url, timeout, payload=None):
        endpoint = url.removeprefix(web.OLLAMA_URL)
        state["calls"].append((endpoint, timeout, payload))
        if endpoint == "/api/version":
            return {"version": "fake-test-runtime"}
        if endpoint == "/api/ps":
            return {"models": []}
        if endpoint == "/api/tags":
            return {"models": state["models"]}
        if endpoint == "/api/show":
            return {"capabilities": state["capabilities"]}
        if endpoint == "/api/generate":
            state["load_started"].set()
            if state["release_load"] is not None:
                assert state["release_load"].wait(5), "test did not release preload"
            if state["load_failure"]:
                raise state["load_failure"]
            return {"done": True}
        assert endpoint == "/api/chat", "web integration must never download a model"
        if state["failure"]:
            raise state["failure"]
        if state["response"] is not None:
            return {"message": {"content": state["response"]}}
        observation = json.loads(payload["messages"][1]["content"])["observation"]
        costs = observation.get("own_costs", observation.get("costs", {}))
        action = min(costs, key=lambda task: (costs[task], task), default=None)
        return {"message": {"content": json.dumps({"action": action})},
                "prompt_eval_count": 7, "eval_count": 3}

    monkeypatch.setattr(ollama, "_request", request)
    monkeypatch.setattr(ollama_selection, "_request", request)
    return state


def launch(milestone="m8", **changes):
    body = {"milestone": milestone, "method": "llm",
            "instance": load_instance(web.ROOT / "examples/allocation.json").to_dict(),
            **({"rounds": 1} if milestone == "m8" else {}), **changes}
    response = client.post("/api/v2/jobs", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def finished(identifier):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(f"/api/v2/jobs/{identifier}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "failed"}:
            return job
        sleep(0.005)
    pytest.fail("short mocked Ollama job did not complete")


@pytest.mark.parametrize("inventory,expected", [("exact", None), ("missing", "model_not_found"),
                                               ("changed", "digest_mismatch")])
def test_inventory_reports_selected_model_presence_and_digest(runtime, inventory, expected):
    if inventory == "missing":
        runtime["models"].clear()
    elif inventory == "changed":
        runtime["models"][0]["digest"] = "different"
    response = client.get("/api/v2/ollama")
    assert response.status_code == 200
    status = response.json()
    assert status["available"] and status["selected_models"]["m8"] == "selected:m8"
    assert bool(status["errors"]) == bool(expected)
    if expected:
        assert any(error.get("error") == expected for error in status["errors"])


@pytest.mark.parametrize("milestone", ["m8", "m10"])
def test_llm_jobs_validate_generated_actions_and_expose_real_client_metrics(runtime, milestone):
    job = finished(launch(milestone)["id"])
    assert job["status"] == "completed", job
    result = job["result"]
    assert result["execution_status"] == "valid"
    assert result["ollama"]["model"] == f"selected:{milestone}"
    assert result["ollama"]["metrics"]["successes"] == 2
    assert result["ollama"]["metrics"]["fallbacks"] == 0
    assert result["ollama"]["metrics"]["generated_tokens"] == 6
    assert result["metrics"]["tasks_completed"] == 2
    assert all(row["action_valid"] and not row["fallback"] for row in result["ollama"]["trace"])
    assignments = [event for event in result["trace"]["events"] if event["kind"] == "assigned"]
    assert len({event["task_id"] for event in assignments}) == 2


@pytest.mark.parametrize("failure,pattern", [
    ("missing", "not installed"), ("changed", "digest changed"),
    ("incompatible", "incompatible"), ("load_timeout", "preload failed: timeout"),
])
def test_model_and_loading_errors_are_explicit_failed_jobs(runtime, failure, pattern):
    options = {}
    if failure == "missing":
        options["model"] = "absent:manual"
    elif failure == "changed":
        runtime["models"][0]["digest"] = "different"
    elif failure == "incompatible":
        runtime["capabilities"] = ["embedding"]
    else:
        runtime["load_failure"] = TimeoutError("slow load")
    job = finished(launch(**options)["id"])
    assert job["status"] == "failed"
    assert job["error"]["kind"] == "ollama_error"
    assert pattern in job["error"]["message"]
    assert not any(endpoint == "/api/chat" for endpoint, _, _ in runtime["calls"])


def test_explicit_installed_model_overrides_frozen_digest(runtime):
    runtime["models"][0]["digest"] = "deliberately-different"
    job = finished(launch(model="selected:m8", preload=False)["id"])
    assert job["status"] == "completed" and job["result"]["execution_status"] == "valid"
    assert job["result"]["ollama"]["preload"] is None


@pytest.mark.parametrize("milestone", ["m8", "m10"])
@pytest.mark.parametrize("failure,expected", [
    ("json", "invalid_json"), ("action", "invalid_action"), ("timeout", "timeout"),
    ("calls", "budget_exhausted"), ("scenario", "scenario_budget_exhausted"),
])
def test_invalid_timeout_and_exhausted_decisions_remain_legal_fallbacks(runtime, monkeypatch, milestone,
                                                                    failure, expected):
    options = {"horizon": 3}
    if failure == "json":
        runtime["response"] = "invalid JSON"
    elif failure == "action":
        runtime["response"] = '{"action":"not-a-task"}'
    elif failure == "timeout":
        runtime["failure"] = TimeoutError("decision timeout")
    elif failure == "calls":
        options["call_budget"] = 0
    else:
        options["scenario_budget"] = 0.1
        clock = count()
        monkeypatch.setattr(ollama, "perf_counter", lambda: float(next(clock)))
    job = finished(launch(milestone, **options)["id"])
    assert job["status"] == "completed", job
    result = job["result"]
    expected_status = "budget_exhausted" if "budget" in expected else "ollama_error"
    assert result["execution_status"] == expected_status
    assert result["ollama"]["metrics"]["errors"][expected] > 0
    assert result["ollama"]["metrics"]["successes"] == 0
    assert all(row["fallback"] and row["action"] is None for row in result["ollama"]["trace"])
    assert result["metrics"]["invalid_decisions"] == 0
    if failure == "calls":
        assert result["ollama"]["metrics"]["calls"] == 0


def test_slow_preload_keeps_job_polling_and_v1_routes_responsive(runtime):
    runtime["release_load"] = Event()
    job = launch()
    try:
        assert runtime["load_started"].wait(2)
        pending = client.get(f"/api/v2/jobs/{job['id']}").json()
        assert pending["status"] == "running" and pending["phase"] == "loading_model"
        assert pending["result"] is None and pending["error"] is None
        assert client.get("/api/examples").status_code == 200
    finally:
        runtime["release_load"].set()
    assert finished(job["id"])["status"] == "completed"


@pytest.mark.parametrize("milestone", ["m8", "m10"])
@pytest.mark.parametrize("failure,expected", [("json", "ollama_error"), ("calls", "budget_exhausted")])
def test_comparison_preserves_llm_failure_status_without_changing_baselines(runtime, milestone,
                                                                           failure, expected):
    options = {"compare": True, "horizon": 3}
    if failure == "json":
        runtime["response"] = "invalid JSON"
    else:
        options["call_budget"] = 0
    result = finished(launch(milestone, **options)["id"])["result"]
    assert result["execution_status"] == expected
    llm = next(row for row in result["comparison"] if row["method"] == "llm")
    assert llm["execution_status"] == expected
    assert all(row["execution_status"] not in {"ollama_error", "budget_exhausted"}
               for row in result["comparison"] if row["method"] != "llm")
