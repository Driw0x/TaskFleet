"""Metrics from engine snapshots and events; elapsed strategy time stays outside traces."""

from collections import Counter

from .engine import Simulation


def metrics(simulation: Simulation) -> dict:
    trace = simulation.trace()
    tasks = trace["current"]["tasks"]
    definitions = {t["id"]: t for t in trace["instance"]["tasks"]}
    completed = [t for t in tasks.values() if t["state"] == "completed"]
    completion_ticks = [t["completed_tick"] for t in completed]
    elapsed = trace["current"]["tick"]
    productive = Counter()
    waiting = Counter()
    unavailable = Counter()
    events_by_tick = {}
    for event in trace["events"]:
        events_by_tick.setdefault(event["tick"], []).append(event)
    for snapshot in trace["snapshots"][1:]:
        delivered = {e["robot_id"] for e in events_by_tick.get(snapshot["tick"], [])
                     if e["kind"] == "delivered"}
        for robot_id, robot in snapshot["robots"].items():
            if robot["unavailable"]:
                unavailable[robot_id] += 1
            elif robot["state"] in {"waiting_pickup", "waiting_delivery"}:
                waiting[robot_id] += 1
            elif robot["state"] != "idle" or robot_id in delivered:
                productive[robot_id] += 1
    utilization = {robot_id: productive[robot_id] / elapsed if elapsed else 0.0
                   for robot_id in trace["current"]["robots"]}
    tardiness = {task_id: max(0, task["completed_tick"] - definitions[task_id]["deadline"])
                 for task_id, task in tasks.items()
                 if task["completed_tick"] is not None and definitions[task_id]["deadline"] is not None}
    overdue = sum(max(0, elapsed - definitions[task_id]["deadline"])
                  for task_id, task in tasks.items()
                  if task["state"] != "completed" and definitions[task_id]["deadline"] is not None
                  and definitions[task_id]["arrival_tick"] <= elapsed)
    message_types = Counter(e["type"] for e in trace["events"] if e["kind"] == "message")
    return {
        "tasks_total": len(tasks),
        "tasks_arrived": sum(t["arrival_tick"] <= elapsed for t in definitions.values()),
        "tasks_completed": len(completed),
        "completion_rate": len(completed) / len(tasks) if tasks else 1.0,
        "observed_ticks": elapsed,
        "makespan": max(completion_ticks, default=0) if len(completed) == len(tasks) else None,
        "last_completion_tick": max(completion_ticks) if completion_ticks else None,
        "total_tardiness": sum(tardiness.values()),
        "late_completed_tasks": sum(value > 0 for value in tardiness.values()),
        "unfinished_tardiness_lower_bound": overdue,
        "task_tardiness": tardiness,
        "distance": sum(e["kind"] == "moved" for e in trace["events"]),
        "robot_utilization": utilization,
        "mean_robot_utilization": sum(utilization.values()) / len(utilization) if utilization else 0.0,
        "productive_robot_ticks": sum(productive.values()),
        "waiting_robot_ticks": sum(waiting.values()),
        "unavailable_robot_ticks": sum(unavailable.values()),
        "strategy_seconds": simulation.strategy_seconds,
        "messages": sum(message_types.values()),
        "message_types": dict(sorted(message_types.items())),
        "invalid_decisions": simulation.invalid_decisions,
    }
