"""Bounded local Ollama decisions, shared by the optional M8 and M10 policies."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class OllamaConfig:
    url: str = "http://localhost:11434"
    model: str = ""
    timeout: float = 5.0
    temperature: float = 0.0
    max_tokens: int = 128
    call_budget: int = 20
    load_timeout: float = 120.0
    scenario_budget: float | None = None
    keep_alive: str = "5m"
    num_ctx: int = 2048
    seed: int = 0
    think: bool | None = None

    def __post_init__(self):
        if (not isinstance(self.url, str) or urlparse(self.url).scheme not in {"http", "https"}
                or not urlparse(self.url).netloc):
            raise ValueError("Ollama URL must use http or https")
        for name in ("timeout", "temperature", "load_timeout", "scenario_budget"):
            value = getattr(self, name)
            if name == "scenario_budget" and value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.timeout == 0 or self.load_timeout == 0:
            raise ValueError("timeout and load_timeout must be positive")
        if not isinstance(self.model, str):
            raise ValueError("model must be a string")
        if not isinstance(self.keep_alive, str) or not self.keep_alive.strip():
            raise ValueError("keep_alive must be a nonempty string")
        if self.think is not None and type(self.think) is not bool:
            raise ValueError("think must be a boolean or None")
        for name, minimum in (("max_tokens", 1), ("call_budget", 0), ("num_ctx", 1), ("seed", 0)):
            if type(getattr(self, name)) is not int or getattr(self, name) < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")


def _request(url: str, timeout: float, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        content = response.read(1_048_577)
    if len(content) > 1_048_576:
        raise ValueError("response_too_large")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("invalid_response")
    return result


def list_models(url: str = "http://localhost:11434", timeout: float = 2.0) -> list[str]:
    """Read installed models only; never start Ollama or download a model."""
    OllamaConfig(url=url, timeout=timeout)
    result = _request(url.rstrip("/") + "/api/tags", timeout)
    return [model["name"] for model in result.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("name"), str)]


_DURATIONS = ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")


def _timings(response: dict) -> tuple[dict, dict]:
    # Missing durations stay absent, never become invented zero measurements.
    durations = {key: response[key] for key in _DURATIONS
                 if type(response.get(key)) is int and response[key] >= 0}
    return durations, {key: value / 1_000_000_000 for key, value in durations.items()}


def _failure(error: Exception, *, model_request=False) -> str:
    if isinstance(error, HTTPError):
        return "model_not_found" if model_request and error.code == 404 else "unavailable"
    if isinstance(error, (OSError, URLError)):
        reason = getattr(error, "reason", error)
        return "timeout" if isinstance(reason, TimeoutError) else "unavailable"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    return str(error) if str(error) in {"response_too_large", "invalid_response"} else "invalid_response"


def _server_error(response: dict) -> str | None:
    error = response.get("error")
    if not error:
        return None
    text = str(error).lower()
    return ("model_not_found" if "model" in text and any(
        marker in text for marker in ("not found", "notfound", "missing")) else "server_error")


def _operation(url: str, endpoint: str, timeout: float, payload=None) -> dict:
    started = perf_counter()
    response, error, detail = {}, None, None
    try:
        response = _request(url.rstrip("/") + endpoint, timeout, payload)
        if not isinstance(response, dict):
            response = {}
            raise ValueError("invalid_response")
        error = _server_error(response)
        detail = str(response["error"]) if error else None
    except (OSError, URLError, ValueError, TypeError, AttributeError) as failure:
        error = _failure(failure, model_request=bool(payload and payload.get("model")))
        detail = str(failure)
    timings_ns, timings_seconds = _timings(response)
    return {"status": "error" if error else "ok", "error": error, "error_detail": detail,
            "endpoint": endpoint, "timeout_seconds": timeout,
            "latency_seconds": perf_counter() - started, "response": response,
            "timings_ns": timings_ns, "timings_seconds": timings_seconds}


def inspect_runtime(url: str = "http://localhost:11434", timeout: float = 2.0) -> dict:
    """Read version/residency without loading, unloading or generating anything."""
    OllamaConfig(url=url, timeout=timeout)
    version = _operation(url, "/api/version", timeout)
    running = _operation(url, "/api/ps", timeout)
    models = running["response"].get("models", [])
    return {"url": url, "version": version["response"].get("version"),
            "running_models": models if isinstance(models, list) else [],
            "errors": [{"endpoint": result["endpoint"], "error": result["error"],
                        "error_detail": result["error_detail"]}
                       for result in (version, running) if result["error"]]}


def discover_models(url: str = "http://localhost:11434", timeout: float = 2.0) -> dict:
    """Inventory installed models and declared capabilities; compatibility is unprobed."""
    result = inspect_runtime(url, timeout)
    tags = _operation(url, "/api/tags", timeout)
    result["models"] = []
    if tags["error"]:
        result["errors"].append({"endpoint": "/api/tags", "error": tags["error"],
                                 "error_detail": tags["error_detail"]})
        return result
    entries = tags["response"].get("models", [])
    if not isinstance(entries, list):
        result["errors"].append({"endpoint": "/api/tags", "error": "invalid_response",
                                 "error_detail": "models must be an array"})
        return result
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        name = entry["name"]
        show = _operation(url, "/api/show", timeout, {"model": name, "verbose": False})
        information = show["response"].get("model_info", {})
        information = information if isinstance(information, dict) else {}
        details = entry.get("details", {})
        details = details if isinstance(details, dict) else {}
        shown_details = show["response"].get("details", {})
        if isinstance(shown_details, dict):
            details = {**details, **shown_details}
        architecture = information.get("general.architecture", details.get("family"))
        context = information.get(f"{architecture}.context_length")
        running = next((model for model in result["running_models"]
                        if isinstance(model, dict) and name in (model.get("name"), model.get("model"))), None)
        result["models"].append({
            "name": name, "size_bytes": entry.get("size"), "digest": entry.get("digest"),
            "architecture": architecture, "parameter_count": information.get("general.parameter_count"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"), "context_length": context,
            "capabilities": show["response"].get("capabilities", []),
            "chat_compatibility": "unprobed", "details": details, "show": show["response"],
            "running": running, "show_error": show["error"],
        })
        if show["error"]:
            result["errors"].append({"endpoint": "/api/show", "model": name,
                                     "error": show["error"], "error_detail": show["error_detail"]})
    return result


def _model_residency(config: OllamaConfig, *, unload: bool) -> dict:
    """Explicit benchmark preparation; never called implicitly by an agent."""
    if not config.model:
        raise ValueError("model_required")
    payload = {"model": config.model, "prompt": "", "stream": False,
               "keep_alive": 0 if unload else config.keep_alive}
    if not unload:
        payload["options"] = {"num_ctx": config.num_ctx, "seed": config.seed}
    result = _operation(config.url, "/api/generate", config.load_timeout, payload)
    result.update(model=config.model, operation="unload" if unload else "preload")
    if result["error"] == "model_not_found":
        result["suggested_command"] = f"ollama pull {config.model}"
    return result


def preload_model(config: OllamaConfig) -> dict:
    """Load an already installed model using an empty prompt and a separate budget."""
    return _model_residency(config, unload=False)


def unload_model(config: OllamaConfig) -> dict:
    """Release model residency only; never delete its files or download a model."""
    return _model_residency(config, unload=True)


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.config = config
        self.metrics = {"calls": 0, "successes": 0, "rejections": 0, "fallbacks": 0,
                        "latency_seconds": 0.0, "prompt_tokens": 0, "generated_tokens": 0,
                        "token_measurements": 0, "errors": {}, "json_responses": 0,
                        "structured_responses": 0, "legal_actions": 0,
                        "preprocess_seconds": 0.0, "decode_seconds": 0.0, "validation_seconds": 0.0,
                        "timings_ns": {}, "duration_measurements": {},
                        "structured_response_rate": None, "legal_action_rate": None,
                        "json_response_rate": None, "fallback_rate": None, "decisions": 0,
                        "generation_tokens_per_second": None, "mean_latency_seconds": None,
                        "scenario_elapsed_seconds": 0.0, "scenario_budget_overrun_seconds": 0.0}
        self.trace: list[dict] = []
        self._scenario_started: float | None = None
        self._deadline: float | None = None
        self._generation_tokens = 0
        self._generation_seconds = 0.0

    def decide(self, observation: dict, allowed_actions, *, context: str = "") -> dict:
        """Return only a validated action; failure selects the first legal action.

        The caller owns the observation boundary and supplies a safe fallback first.
        No simulation, other-agent state or future configuration reaches this client.
        The scenario clock starts on the first decision, excluding explicit preload.
        Remaining time bounds the socket timeout. DNS/socket behavior can overrun the
        wall budget; overruns are recorded and late answers never become decisions.
        """
        actions = list(allowed_actions)
        if not actions or any(a is not None and not isinstance(a, str) for a in actions):
            raise ValueError("allowed_actions must contain strings or None and be nonempty")
        started = perf_counter()
        if self._scenario_started is None:
            self._scenario_started = started
            if self.config.scenario_budget is not None:
                self._deadline = started + self.config.scenario_budget
        error = None
        error_detail = None
        action = actions[0]
        response = {}
        content = None
        json_valid = structured_valid = action_legal = False
        preprocess_seconds = decode_seconds = validation_seconds = request_seconds = 0.0
        request_timeout = None
        if self.metrics["calls"] >= self.config.call_budget:
            error = "budget_exhausted"
        elif self._deadline is not None and started >= self._deadline:
            error = "scenario_budget_exhausted"
        elif not self.config.model:
            error = "model_required"
        else:
            preparing = perf_counter()
            payload = {
                "model": self.config.model, "stream": False, "keep_alive": self.config.keep_alive,
                "format": {"type": "object", "properties": {"action": {"enum": actions}},
                           "required": ["action"], "additionalProperties": False},
                "messages": [
                    {"role": "system", "content": "Choose one legal local action. Return only JSON "
                     "with exactly one key, action. Never invent actions. " + context},
                    {"role": "user", "content": json.dumps(
                        {"observation": observation, "allowed_actions": actions}, sort_keys=True)},
                ],
                "options": {"temperature": self.config.temperature,
                            "num_predict": self.config.max_tokens,
                            "num_ctx": self.config.num_ctx, "seed": self.config.seed},
            }
            if self.config.think is not None:
                payload["think"] = self.config.think
            preprocess_seconds = perf_counter() - preparing
            try:
                request_timeout = self.config.timeout
                if self._deadline is not None:
                    request_timeout = min(request_timeout, self._deadline - perf_counter())
                if request_timeout <= 0:
                    raise ValueError("scenario_budget_exhausted")
                self.metrics["calls"] += 1
                requesting = perf_counter()
                try:
                    response = _request(self.config.url.rstrip("/") + "/api/chat", request_timeout, payload)
                finally:
                    request_seconds = perf_counter() - requesting
                if not isinstance(response, dict):
                    response = {}
                    raise ValueError("invalid_response")
                server_error = _server_error(response)
                if server_error:
                    raise ValueError(server_error)
                content = response.get("message", {}).get("content", "")
                if not content:
                    raise ValueError("empty_response")
                decoding = perf_counter()
                try:
                    decision = json.loads(content)
                    json_valid = True
                finally:
                    decode_seconds = perf_counter() - decoding
                validating = perf_counter()
                structured_valid = (isinstance(decision, dict) and set(decision) == {"action"}
                                    and (decision["action"] is None or isinstance(decision["action"], str)))
                action_legal = structured_valid and decision["action"] in actions
                validation_seconds = perf_counter() - validating
                if not action_legal:
                    raise ValueError("invalid_action")
                action = decision["action"]
            except (OSError, URLError) as failure:
                error = _failure(failure, model_request=True)
                error_detail = str(failure)
            except json.JSONDecodeError as failure:
                error = "invalid_json"
                error_detail = str(failure)
            except (ValueError, TypeError, AttributeError) as failure:
                error = str(failure) if str(failure) in {
                    "empty_response", "invalid_action", "server_error", "response_too_large",
                    "invalid_response", "model_not_found", "scenario_budget_exhausted"} else "invalid_response"
                error_detail = str(failure)
        finished = perf_counter()
        elapsed = finished - started
        scenario_elapsed = finished - self._scenario_started
        overrun = max(0.0, finished - self._deadline) if self._deadline is not None else 0.0
        if overrun and error is None:
            error, action = "scenario_budget_exhausted", actions[0]
        tokens = {"prompt_tokens": None, "generated_tokens": None}
        for source, target in (("prompt_eval_count", "prompt_tokens"),
                               ("eval_count", "generated_tokens")):
            count = response.get(source)
            if type(count) is int and count >= 0:
                self.metrics[target] += count
                tokens[target] = count
        if any(type(response.get(key)) is int and response[key] >= 0
               for key in ("prompt_eval_count", "eval_count")):
            self.metrics["token_measurements"] += 1
        self.metrics["latency_seconds"] += elapsed
        self.metrics["decisions"] += 1
        self.metrics["json_responses"] += json_valid
        self.metrics["structured_responses"] += structured_valid
        self.metrics["preprocess_seconds"] += preprocess_seconds
        self.metrics["decode_seconds"] += decode_seconds
        self.metrics["validation_seconds"] += validation_seconds
        self.metrics["scenario_elapsed_seconds"] = scenario_elapsed
        self.metrics["scenario_budget_overrun_seconds"] = max(
            overrun, self.metrics["scenario_budget_overrun_seconds"])
        timings_ns, timings_seconds = _timings(response)
        for key, value in timings_ns.items():
            self.metrics["timings_ns"][key] = self.metrics["timings_ns"].get(key, 0) + value
            self.metrics["duration_measurements"][key] = self.metrics["duration_measurements"].get(key, 0) + 1
        if (timings_seconds.get("eval_duration", 0) > 0
                and type(response.get("eval_count")) is int and response["eval_count"] >= 0):
            self._generation_tokens += max(0, response["eval_count"])
            self._generation_seconds += timings_seconds["eval_duration"]
            self.metrics["generation_tokens_per_second"] = self._generation_tokens / self._generation_seconds
        if error:
            self.metrics["rejections"] += 1
            self.metrics["fallbacks"] += 1
            errors = self.metrics["errors"]
            errors[error] = errors.get(error, 0) + 1
        else:
            self.metrics["successes"] += 1
            self.metrics["legal_actions"] += 1
        if self.metrics["calls"]:
            self.metrics["json_response_rate"] = self.metrics["json_responses"] / self.metrics["calls"]
            self.metrics["structured_response_rate"] = self.metrics["structured_responses"] / self.metrics["calls"]
            self.metrics["legal_action_rate"] = self.metrics["legal_actions"] / self.metrics["calls"]
            self.metrics["mean_latency_seconds"] = self.metrics["latency_seconds"] / self.metrics["calls"]
        self.metrics["fallback_rate"] = self.metrics["fallbacks"] / self.metrics["decisions"]
        result = {"action": action, "fallback": error is not None, "error": error}
        self.trace.append({"observation": deepcopy(observation), "allowed_actions": actions,
                           **result, **tokens, "latency_seconds": elapsed,
                           "raw_response": deepcopy(response), "raw_content": content,
                           "json_valid": json_valid, "structured_valid": structured_valid,
                           "action_legal": action_legal, "action_valid": action_legal,
                           "error_detail": error_detail,
                           "metadata": {key: deepcopy(value) for key, value in response.items() if key != "message"},
                           "timings_ns": timings_ns, "timings_seconds": timings_seconds,
                           "preprocess_seconds": preprocess_seconds, "decode_seconds": decode_seconds,
                           "validation_seconds": validation_seconds, "request_seconds": request_seconds,
                           "request_timeout_seconds": request_timeout,
                           "request_timeout_overrun_seconds": max(0.0, request_seconds - request_timeout)
                           if request_timeout is not None and request_timeout > 0 else 0.0,
                           "scenario_elapsed_seconds": scenario_elapsed,
                           "scenario_remaining_seconds": max(0.0, self._deadline - finished)
                           if self._deadline is not None else None,
                           "scenario_budget_overrun_seconds": overrun,
                           "suggested_command": f"ollama pull {self.config.model}" if error == "model_not_found" else None})
        return result


def probe_model(config: OllamaConfig) -> dict:
    """One structured decision using the production client, with no implicit preload."""
    client = OllamaClient(config)
    decision = client.decide({"probe": "Select probe to verify a structured local decision."},
                             [None, "probe"], context="Choose probe.")
    return {"config": asdict(config), "decision": decision, "metrics": client.metrics, "trace": client.trace}
