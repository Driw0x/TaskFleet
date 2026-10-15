"""Load one frozen benchmark choice; explicit CLI settings take precedence."""

from dataclasses import fields
import json
from pathlib import Path

from .ollama import OllamaConfig, _request


def resolve_config(usage, *, selection=None, **overrides):
    if usage not in {"m8", "m10"}:
        raise ValueError("Ollama usage must be m8 or m10")
    values, chosen = {}, None
    manual_model = overrides.get("model")
    if selection is not None:
        report = json.loads(Path(selection).read_text(encoding="utf-8"))
        if (not isinstance(report, dict) or type(report.get("schema")) is not int or report["schema"] != 1
                or not isinstance(report.get("selection"), dict)):
            raise ValueError("unsupported Ollama benchmark selection report")
        chosen = report["selection"].get(usage)
        if not isinstance(chosen, dict):
            raise ValueError(f"benchmark has no {usage} selection entry")
        if chosen.get("model") is None and manual_model is None:
            raise ValueError(f"no model selected for {usage}; inspect validation failures or specify --model")
        if chosen.get("config") is not None:
            if not isinstance(chosen["config"], dict):
                raise ValueError("invalid selected Ollama configuration")
            values.update(chosen["config"])
            if chosen.get("model") != values.get("model"):
                raise ValueError("selected model and configuration disagree")
    allowed = {field.name for field in fields(OllamaConfig)}
    if values.keys() - allowed or overrides.keys() - allowed:
        raise ValueError("unknown Ollama configuration field")
    values.update({key: value for key, value in overrides.items() if value is not None})
    config = OllamaConfig(**values)
    if selection is not None:
        digest = chosen.get("model_digest") if manual_model is None else None
        if manual_model is None and (not isinstance(digest, str) or not digest.strip()):
            raise ValueError("selected model digest is missing; rerun benchmark or explicitly choose --model")
        # Read-only preflight: never pull, substitute or start a server.
        try:
            tags = _request(config.url.rstrip("/") + "/api/tags", min(config.timeout, 5))
        except (OSError, ValueError) as error:
            raise ValueError(f"cannot verify selected Ollama model: {error}") from error
        if not isinstance(tags.get("models"), list):
            raise ValueError("invalid Ollama model inventory")
        installed = next((model for model in tags["models"]
                          if isinstance(model, dict) and model.get("name") == config.model), None)
        if installed is None:
            raise ValueError(f"Ollama model {config.model!r} is not installed. "
                             f"Install explicitly with: ollama pull {config.model}; "
                             "or choose another --model. No download performed.")
        if digest and installed.get("digest") != digest:
            raise ValueError("selected model digest changed; rerun benchmark or explicitly choose --model")
    return config
