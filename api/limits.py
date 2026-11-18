"""Shared limits for untrusted warehouse instances accepted by the web API."""

import json

from taskfleet.instances import from_dict


MAX_JSON_BYTES = 100_000
MAX_WIDTH = 20
MAX_HEIGHT = 20
MAX_HORIZON = 300
MAX_SOLVER_BUDGET = 1.0
MAX_SEED = 2_147_483_647
MAX_ARRAY_LENGTHS = {
    "robots": 8,
    "tasks": 24,
    "obstacles": 400,
    "resources": 30,
    "unavailability": 50,
}


def _unique_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"JSON: duplicate field {key!r}")
        data[key] = value
    return data


def validate_web_payload_size(data) -> None:
    try:
        size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
    except (TypeError, ValueError) as error:
        raise ValueError(f"Instance is not valid JSON: {error}") from error
    if size > MAX_JSON_BYTES:
        raise ValueError("Instance exceeds the 100 KB web limit")


def parse_web_instance(value: dict | str):
    if isinstance(value, str):
        if len(value.encode()) > MAX_JSON_BYTES:
            raise ValueError("Instance exceeds the 100 KB web limit")
        try:
            data = json.loads(value, object_pairs_hook=_unique_keys)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON at line {error.lineno}: {error.msg}") from error
    else:
        data = value
        validate_web_payload_size(data)
    if not isinstance(data, dict):
        raise ValueError("instance: expected an object")
    missing = {"width", "height", "robots", "tasks"} - data.keys()
    if missing:
        raise ValueError(f"instance: missing fields {sorted(missing)}")
    for name, maximum in (("width", MAX_WIDTH), ("height", MAX_HEIGHT)):
        if type(data.get(name)) is not int or not 1 <= data[name] <= maximum:
            raise ValueError(f"{name} must be an integer in [1, {maximum}]")
    for name, maximum in MAX_ARRAY_LENGTHS.items():
        entries = data.get(name, [])
        if not isinstance(entries, list) or len(entries) > maximum:
            raise ValueError(f"{name} exceeds the web limit of {maximum}")
        if name in {"robots", "tasks", "resources"} and any(
            not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
            or len(entry["id"]) > 100 for entry in entries
        ):
            raise ValueError(f"{name}: IDs must be strings of at most 100 characters")
        for entry in entries:
            if isinstance(entry, dict):
                for field in ("capacity", "load", "deadline", "arrival_tick", "start_tick", "end_tick"):
                    number = entry.get(field)
                    if type(number) is int and not 0 <= number <= 1_000_000:
                        raise ValueError(f"{name}.{field}: web values must be in [0, 1000000]")
    seed = data.get("seed")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= MAX_SEED):
        raise ValueError(f"seed must be an integer in [0, {MAX_SEED}]")
    horizon = data.get("horizon", 100)
    if type(horizon) is not int or not 0 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be an integer in [0, {MAX_HORIZON}]")
    if any(type(task.get("handling_ticks", 1)) is not int
           or not 1 <= task.get("handling_ticks", 1) <= 20 for task in data.get("tasks", [])):
        raise ValueError("handling_ticks must be in [1, 20]")
    return from_dict(data)
