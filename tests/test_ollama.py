import json
from urllib.error import HTTPError, URLError

import pytest

from taskfleet import ollama
from taskfleet.ollama import (OllamaClient, OllamaConfig, discover_models, inspect_runtime,
                              list_models, preload_model, probe_model, unload_model)


def client(**kwargs):
    return OllamaClient(OllamaConfig(model="test-local-model", **kwargs))


def test_valid_structured_action_tokens_and_local_payload(monkeypatch):
    payloads = []

    def response(url, timeout, payload):
        payloads.append(payload)
        assert url == "http://localhost:11434/api/chat" and timeout == 5
        return {"message": {"content": '{"action":"t1"}'},
                "prompt_eval_count": 11, "eval_count": 5}

    monkeypatch.setattr(ollama, "_request", response)
    model = client()
    observation = {"robot": "r1", "own_costs": {"t1": 2}}
    assert model.decide(observation, [None, "t1"]) == {
        "action": "t1", "fallback": False, "error": None}
    assert model.metrics["calls"] == model.metrics["successes"] == 1
    assert model.metrics["prompt_tokens"] == 11 and model.metrics["generated_tokens"] == 5
    assert model.metrics["token_measurements"] == 1
    assert model.metrics["latency_seconds"] >= 0
    payload = payloads[0]
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0, "num_predict": 128, "num_ctx": 2048, "seed": 0}
    assert payload["keep_alive"] == "5m"
    assert "think" not in payload
    assert json.loads(payload["messages"][1]["content"])["observation"] == observation
    observation["robot"] = "changed"
    assert model.trace[0]["observation"]["robot"] == "r1"


@pytest.mark.parametrize("response,error", [
    ({"message": {"content": "not JSON"}}, "invalid_json"),
    ({"message": {"content": '{"action":"forbidden"}'}}, "invalid_action"),
    ({"message": {"content": '{"action":true}'}}, "invalid_action"),
    ({"message": {"content": '{"action":null,"extra":1}'}}, "invalid_action"),
    ({"message": {"content": "[]"}}, "invalid_action"),
    ({"message": {"content": ""}}, "empty_response"),
    ({}, "empty_response"),
    ({"message": []}, "invalid_response"),
    ({"message": None}, "invalid_response"),
    ({"error": "model missing"}, "model_not_found"),
])
def test_invalid_responses_use_traceable_legal_fallback(monkeypatch, response, error):
    monkeypatch.setattr(ollama, "_request", lambda *args: response)
    model = client()
    assert model.decide({}, [None, "t"]) == {"action": None, "fallback": True, "error": error}
    assert model.metrics["fallbacks"] == model.metrics["rejections"] == 1
    assert model.metrics["errors"] == {error: 1}
    assert model.trace[0]["error"] == error


@pytest.mark.parametrize("failure,error", [
    (TimeoutError(), "timeout"),
    (URLError(TimeoutError()), "timeout"),
    (URLError("connection refused"), "unavailable"),
    (ConnectionResetError("reset while reading"), "unavailable"),
    (HTTPError("http://localhost", 404, "missing model", {}, None), "model_not_found"),
    (json.JSONDecodeError("invalid", "x", 0), "invalid_json"),
])
def test_network_and_decoding_errors_are_bounded_fallbacks(monkeypatch, failure, error):
    def fail(*args):
        raise failure
    monkeypatch.setattr(ollama, "_request", fail)
    model = client(timeout=0.1)
    result = model.decide({}, [None])
    assert result["error"] == error and result["fallback"]
    assert model.metrics["calls"] == 1
    assert model.metrics["token_measurements"] == 0


def test_budget_does_not_make_extra_requests(monkeypatch):
    requests = []
    monkeypatch.setattr(ollama, "_request", lambda *args: requests.append(args) or {
        "message": {"content": '{"action":null}'}})
    model = client(call_budget=1)
    assert not model.decide({}, [None])["fallback"]
    assert model.decide({}, [None])["error"] == "budget_exhausted"
    assert model.metrics["calls"] == len(requests) == 1
    assert client(call_budget=0).decide({}, [None])["error"] == "budget_exhausted"
    assert OllamaClient(OllamaConfig()).decide({}, [None])["error"] == "model_required"


@pytest.mark.parametrize("actions", [[], [1], [False], [{}]])
def test_invalid_domains(actions):
    with pytest.raises(ValueError, match="allowed_actions"):
        client().decide({}, actions)


@pytest.mark.parametrize("options", [
    {"url": "file:///tmp/x"}, {"url": 42}, {"timeout": 0}, {"timeout": float("inf")},
    {"temperature": -1}, {"max_tokens": 0}, {"call_budget": -1},
    {"call_budget": True}, {"model": 12},
    {"load_timeout": 0}, {"scenario_budget": -1}, {"scenario_budget": float("inf")},
    {"keep_alive": ""}, {"num_ctx": 0}, {"seed": -1},
    {"think": "false"}, {"think": 0},
])
def test_invalid_configuration(options):
    with pytest.raises(ValueError):
        OllamaConfig(**options)


def test_tags_uses_installed_names_only(monkeypatch):
    calls = []
    monkeypatch.setattr(ollama, "_request", lambda *args: calls.append(args) or {
        "models": [{"name": "installed:latest"}, {}, {"name": 4}]})
    assert list_models(timeout=0.2) == ["installed:latest"]
    assert calls == [("http://localhost:11434/api/tags", 0.2)]


def test_http_request_response_size_and_top_level_validation(monkeypatch):
    class Response:
        content = b'{"models":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, maximum):
            assert maximum == 1_048_577
            return self.content

    monkeypatch.setattr(ollama, "urlopen", lambda *args, **kwargs: Response())
    assert ollama._request("http://localhost/api/tags", 0.1) == {"models": []}
    Response.content = b"[]"
    with pytest.raises(ValueError, match="invalid_response"):
        ollama._request("http://localhost/api/tags", 0.1)
    Response.content = b" " * 1_048_577
    with pytest.raises(ValueError, match="response_too_large"):
        ollama._request("http://localhost/api/tags", 0.1)


def test_discovery_declared_metadata_and_residency_never_generate_or_pull(monkeypatch):
    requests = []

    def response(url, timeout, payload=None):
        requests.append((url, timeout, payload))
        if url.endswith("/version"):
            return {"version": "0.12.test"}
        if url.endswith("/ps"):
            return {"models": [{"name": "local:7b", "size_vram": 123, "context_length": 2048}]}
        if url.endswith("/tags"):
            return {"models": [{"name": "local:7b", "size": 456, "digest": "sha256:abc",
                                "details": {"family": "qwen2", "parameter_size": "7B",
                                            "quantization_level": "Q4_K_M"}}]}
        assert url.endswith("/show") and payload == {"model": "local:7b", "verbose": False}
        return {"model_info": {"general.architecture": "qwen2", "general.parameter_count": 7_000_000_000,
                               "qwen2.context_length": 32768}, "capabilities": ["completion"]}

    monkeypatch.setattr(ollama, "_request", response)
    result = discover_models("http://127.0.0.1:11434", 0.5)
    assert result["version"] == "0.12.test" and not result["errors"]
    model = result["models"][0]
    assert model["size_bytes"] == 456 and model["digest"] == "sha256:abc"
    assert model["architecture"] == "qwen2" and model["parameter_count"] == 7_000_000_000
    assert model["quantization"] == "Q4_K_M" and model["context_length"] == 32768
    assert model["running"]["size_vram"] == 123 and model["capabilities"] == ["completion"]
    assert model["chat_compatibility"] == "unprobed"
    assert len(requests) == 4 and all(item[1] == 0.5 for item in requests)


def test_discovery_preserves_partial_server_failures(monkeypatch):
    def response(url, timeout, payload=None):
        if url.endswith("/tags"):
            return {"models": [{"name": "local:missing-details"}]}
        if url.endswith("/show"):
            raise HTTPError(url, 404, "model missing", {}, None)
        raise URLError("server endpoint unavailable")

    monkeypatch.setattr(ollama, "_request", response)
    result = discover_models(timeout=0.1)
    assert result["version"] is None and result["running_models"] == []
    assert len(result["models"]) == 1 and len(result["errors"]) == 3
    model = result["models"][0]
    assert model["context_length"] is None and model["parameter_count"] is None
    assert model["show_error"] == "model_not_found"
    monkeypatch.setattr(ollama, "_request", lambda *args: {"models": "malformed"})
    result = discover_models()
    assert result["models"] == [] and result["errors"][-1]["error"] == "invalid_response"


def test_preload_unload_separate_budget_and_no_implicit_residency(monkeypatch):
    requests = []

    def response(url, timeout, payload):
        requests.append((url, timeout, payload))
        return {"done": True, "load_duration": 2_000_000_000, "total_duration": 2_010_000_000}

    monkeypatch.setattr(ollama, "_request", response)
    config = OllamaConfig(model="local:7b", timeout=5, load_timeout=90, keep_alive="2m", num_ctx=1024, seed=11)
    model = OllamaClient(config)
    loaded = preload_model(config)
    assert loaded["status"] == "ok" and loaded["operation"] == "preload"
    assert loaded["timings_seconds"]["load_duration"] == 2
    assert loaded["timings_ns"]["total_duration"] == 2_010_000_000
    assert "eval_duration" not in loaded["timings_ns"]
    assert requests[0] == ("http://localhost:11434/api/generate", 90,
                            {"model": "local:7b", "prompt": "", "stream": False,
                             "keep_alive": "2m", "options": {"num_ctx": 1024, "seed": 11}})
    assert model.metrics["calls"] == 0 and model._scenario_started is None
    released = unload_model(config)
    assert released["status"] == "ok" and requests[-1][2]["keep_alive"] == 0
    assert requests[-1][2]["prompt"] == "" and "options" not in requests[-1][2]
    assert len(requests) == 2


def test_missing_model_preload_is_reported_without_download(monkeypatch):
    calls = []
    monkeypatch.setattr(ollama, "_request", lambda *args: calls.append(args) or {
        "error": "model 'absent:7b' not found"})
    result = preload_model(OllamaConfig(model="absent:7b"))
    assert result["error"] == "model_not_found" and result["status"] == "error"
    assert len(calls) == 1 and calls[0][0].endswith("/api/generate")
    with pytest.raises(ValueError, match="model_required"):
        preload_model(OllamaConfig())


def test_probe_uses_production_decision_validation(monkeypatch):
    requests = []
    monkeypatch.setattr(ollama, "_request", lambda *args: requests.append(args) or {
        "message": {"content": '{"action":"probe"}'}})
    result = probe_model(OllamaConfig(model="local"))
    assert result["decision"] == {"action": "probe", "fallback": False, "error": None}
    assert result["metrics"]["legal_actions"] == 1 and result["trace"][0]["structured_valid"]
    assert len(requests) == 1 and requests[0][0].endswith("/api/chat")


@pytest.mark.parametrize("thinking", [False, True])
def test_explicit_thinking_mode_propagates_to_probe_request(monkeypatch, thinking):
    requests = []
    monkeypatch.setattr(ollama, "_request", lambda *args: requests.append(args) or {
        "message": {"content": '{"action":"probe"}'}})
    result = probe_model(OllamaConfig(model="local", think=thinking))
    assert requests[0][2]["think"] is thinking
    assert result["config"]["think"] is thinking


def test_scenario_deadline_starts_after_preload_and_bounds_remaining_timeout(monkeypatch):
    clock = [100.0]
    timeouts = []
    monkeypatch.setattr(ollama, "perf_counter", lambda: clock[0])

    def response(url, timeout, payload):
        timeouts.append(timeout)
        clock[0] += 20 if url.endswith("/generate") else 2
        return {"message": {"content": '{"action":"t"}'}}

    monkeypatch.setattr(ollama, "_request", response)
    config = OllamaConfig(model="local", timeout=5, load_timeout=30, scenario_budget=3)
    model = OllamaClient(config)
    preload_model(config)
    assert not model.decide({}, [None, "t"])["fallback"]
    late = model.decide({}, [None, "t"])
    assert late == {"action": None, "fallback": True, "error": "scenario_budget_exhausted"}
    assert timeouts == [30, 3, 1]
    assert model.metrics["calls"] == 2 and model.metrics["legal_actions"] == 1
    assert model.metrics["scenario_elapsed_seconds"] == 4
    assert model.metrics["scenario_budget_overrun_seconds"] == 1
    assert model.trace[-1]["action_legal"] and model.trace[-1]["fallback"]
    assert model.trace[-1]["scenario_remaining_seconds"] == 0
    assert model.decide({}, [None])["error"] == "scenario_budget_exhausted"
    assert len(timeouts) == 3


def test_zero_scenario_budget_never_attempts_network(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda *args: pytest.fail("unexpected request"))
    model = client(scenario_budget=0)
    assert model.decide({}, [None])["error"] == "scenario_budget_exhausted"
    assert model.metrics["calls"] == 0 and model.metrics["fallback_rate"] == 1
    assert model.metrics["legal_action_rate"] is None


def test_raw_response_timings_and_schema_legality_are_measured_separately(monkeypatch):
    response = {"model": "local", "done_reason": "stop", "message": {"content": '{"action":"outside"}'},
                "prompt_eval_count": 12, "eval_count": 5, "load_duration": 100_000_000,
                "prompt_eval_duration": 200_000_000, "eval_duration": 250_000_000,
                "total_duration": 600_000_000}
    monkeypatch.setattr(ollama, "_request", lambda *args: response)
    model = client()
    assert model.decide({}, [None])["error"] == "invalid_action"
    assert model.metrics["json_responses"] == model.metrics["structured_responses"] == 1
    assert model.metrics["legal_actions"] == 0 and model.metrics["structured_response_rate"] == 1
    assert model.metrics["legal_action_rate"] == 0
    assert model.metrics["generation_tokens_per_second"] == 20
    trace = model.trace[0]
    assert trace["json_valid"] and trace["structured_valid"] and not trace["action_legal"]
    assert trace["raw_response"] == response and trace["raw_content"] == '{"action":"outside"}'
    assert trace["metadata"]["done_reason"] == "stop" and "message" not in trace["metadata"]
    assert trace["timings_seconds"]["load_duration"] == 0.1
    assert trace["decode_seconds"] >= 0 and trace["validation_seconds"] >= 0
    assert model.metrics["duration_measurements"]["eval_duration"] == 1


def test_partial_metrics_never_invent_absent_tokens_or_durations(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda *args: {
        "message": {"content": "[]"}, "eval_count": 2, "eval_duration": True})
    model = client()
    assert model.decide({}, [None])["error"] == "invalid_action"
    trace = model.trace[0]
    assert trace["json_valid"] and not trace["structured_valid"]
    assert trace["generated_tokens"] == 2 and trace["prompt_tokens"] is None
    assert trace["timings_ns"] == trace["timings_seconds"] == {}
    assert model.metrics["generation_tokens_per_second"] is None
    assert model.metrics["timings_ns"] == model.metrics["duration_measurements"] == {}


def test_runtime_inspection_never_loads_models(monkeypatch):
    endpoints = []
    monkeypatch.setattr(ollama, "_request", lambda url, timeout, payload=None:
                        endpoints.append(url) or {"models": []})
    assert inspect_runtime()["running_models"] == []
    assert endpoints == ["http://localhost:11434/api/version", "http://localhost:11434/api/ps"]
