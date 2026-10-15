from dataclasses import asdict
import json

import pytest

from taskfleet import ollama_selection
from taskfleet.ollama import OllamaConfig
from taskfleet.ollama_selection import resolve_config


def report(tmp_path, *, model="chosen:7b", config=None):
    selected = {"model": model, "model_digest": "frozen-digest",
                "config": asdict(OllamaConfig(model=model, timeout=30, call_budget=8))
                if model is not None and config is None else config}
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema": 1, "selection": {"m8": selected, "m10": selected}}),
                    encoding="utf-8")
    return path


def test_frozen_choice_and_explicit_overrides_without_download(tmp_path, monkeypatch):
    requests = []

    def tags(url, timeout):
        requests.append((url, timeout))
        assert url.endswith("/api/tags")
        return {"models": [{"name": "chosen:7b", "digest": "frozen-digest"},
                           {"name": "manual:4b", "digest": "other"}]}

    monkeypatch.setattr(ollama_selection, "_request", tags)
    path = report(tmp_path)
    chosen = resolve_config("m8", selection=path)
    assert chosen.model == "chosen:7b" and chosen.timeout == 30 and chosen.call_budget == 8
    manual = resolve_config("m10", selection=path, model="manual:4b", timeout=12)
    assert manual.model == "manual:4b" and manual.timeout == 12 and manual.call_budget == 8
    assert len(requests) == 2 and all("pull" not in url for url, _ in requests)
    assert resolve_config("m8", model="offline-test").timeout == 5
    assert len(requests) == 2


def test_absent_model_and_changed_digest_fail_closed(tmp_path, monkeypatch):
    path = report(tmp_path)
    monkeypatch.setattr(ollama_selection, "_request", lambda *args: {"models": []})
    with pytest.raises(ValueError, match="ollama pull chosen:7b"):
        resolve_config("m8", selection=path)
    monkeypatch.setattr(ollama_selection, "_request", lambda *args: {
        "models": [{"name": "chosen:7b", "digest": "changed"}]})
    with pytest.raises(ValueError, match="digest changed"):
        resolve_config("m10", selection=path)


def test_no_eligible_model_manual_override_and_server_error(tmp_path, monkeypatch):
    path = report(tmp_path, model=None)
    monkeypatch.setattr(ollama_selection, "_request", lambda *args: {
        "models": [{"name": "manual:4b"}]})
    with pytest.raises(ValueError, match="no model selected"):
        resolve_config("m8", selection=path)
    assert resolve_config("m8", selection=path, model="manual:4b").model == "manual:4b"

    def unavailable(*args):
        raise TimeoutError("server down")

    monkeypatch.setattr(ollama_selection, "_request", unavailable)
    with pytest.raises(ValueError, match="cannot verify"):
        resolve_config("m8", selection=path, model="manual:4b")


def test_missing_digest_and_invalid_inventory_are_explicit_errors(tmp_path, monkeypatch):
    path = report(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["selection"]["m8"]["model_digest"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="digest is missing"):
        resolve_config("m8", selection=path)
    monkeypatch.setattr(ollama_selection, "_request", lambda *args: {"models": None})
    with pytest.raises(ValueError, match="invalid Ollama model inventory"):
        resolve_config("m8", selection=path, model="manual:4b")


@pytest.mark.parametrize("payload", [[], {}, {"schema": 9, "selection": {}},
    {"schema": True, "selection": {}},
    {"schema": 1, "selection": {}},
    {"schema": 1, "selection": {"m8": {"model": "a", "config": []}}},
    {"schema": 1, "selection": {"m8": {"model": "a", "config": {"model": "b"}}}},
    {"schema": 1, "selection": {"m8": {"model": "a", "config": {"model": "a", "unknown": 1}}}},
    {"schema": 1, "selection": {"m8": {"model": "a", "config": {"model": "a", "url": 42}}}},
])
def test_invalid_selection_report(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_config("m8", selection=path)


def test_both_cli_entrypoints_load_frozen_choice(tmp_path, monkeypatch, capsys):
    from taskfleet.dcop import main as dcop_main
    from taskfleet.marl import main as marl_main

    path = report(tmp_path, config=asdict(OllamaConfig(model="chosen:7b", call_budget=0)))
    monkeypatch.setattr(ollama_selection, "_request", lambda *args: {
        "models": [{"name": "chosen:7b", "digest": "frozen-digest"}]})
    dcop_output, marl_output = tmp_path / "dcop.json", tmp_path / "marl.json"
    assert dcop_main(["examples/allocation.json", "--method", "llm", "--selection", str(path),
                      "--rounds", "1", "--output", str(dcop_output)]) == 0
    assert marl_main(["evaluate", "examples/allocation.json", "--policy", "llm", "--selection",
                      str(path), "--json", str(marl_output)]) == 0
    dcop = json.loads(dcop_output.read_text(encoding="utf-8"))
    marl = json.loads(marl_output.read_text(encoding="utf-8"))
    assert dcop["llm_metrics"]["calls"] == marl["llm"]["calls"] == 0
    assert dcop["selection_source"] == marl["selection_source"] == str(path)
    assert marl["llm_actions_accepted"] == 0
