"""Web result adapters for the existing M7 and isolated M9 implementations."""

from taskfleet.instances import from_dict
from taskfleet.mapf import solve
from taskfleet.metrics import metrics
from taskfleet.scheduling import (
    execute_schedule, optimize_priority, run_priority, run_rolling, solve_schedule,
)


def _execution(simulation):
    simulation.check_invariants()
    return {
        "execution_status": "valid" if simulation.all_tasks_completed else "incomplete",
        "metrics": metrics(simulation),
        "trace": simulation.trace(),
    }


def _nominal(body, instance):
    plan = solve_schedule(instance, budget=body.solver_budget)
    result = {"method": "nominal", "plan": plan,
              "information": "Offline oracle: all tasks and the declared outage calendar are known."}
    if not plan["feasible"]:
        # No unchecked plan is presented as an execution, even when a budget stops search.
        result.update(execution_status="infeasible" if plan["status"] == "INFEASIBLE"
                      else "budget_exhausted", metrics={})
        return result
    simulation, execution = execute_schedule(instance, plan)
    result.update(_execution(simulation), execution=execution,
                  search_budget_exhausted=plan["status"] == "UNKNOWN")
    if not execution["executable"]:
        result["execution_status"] = "invalid"
    return result


def _rolling(body, instance):
    simulation, rolling = run_rolling(instance, window=body.window,
                                      frequency=body.frequency, budget=body.solver_budget)
    result = {"method": "rolling", **_execution(simulation), "rolling": rolling,
              "information": rolling["information"]}
    snapshots = {row["tick"]: row for row in result["trace"]["snapshots"]}
    for plan in rolling["plans"]:
        plan["committed"] = [
            {"robot_id": robot_id, "task_id": robot["task_id"]}
            for robot_id, robot in snapshots[plan["start_tick"]]["robots"].items()
            if robot["task_id"] is not None]
    # Recover dispatch provenance from the recorded plans and actual engine events.
    # run_rolling uses FIFO only when the current plan has no objective/solution.
    rolling["dispatches"] = []
    for event in result["trace"]["events"]:
        if event["kind"] == "assigned":
            plan = next(plan for plan in reversed(rolling["plans"])
                        if plan["start_tick"] <= event["tick"])
            rolling["dispatches"].append({
                **{key: event[key] for key in ("tick", "robot_id", "task_id")},
                "source": "fifo" if plan["objective"] is None else "plan",
                "plan_start_tick": plan["start_tick"]})
    # Completion and search exhaustion are distinct: a FIFO fallback may finish tasks.
    result["search_budget_exhausted"] = any(
        plan["status"] == "UNKNOWN" for plan in rolling["plans"])
    return result


def _scenario_runs(instance, policy, scenarios):
    runs = []
    for index, calendar in enumerate(scenarios):
        data = instance.to_dict()
        data["unavailability"] = calendar
        scenario = from_dict(data)
        simulation = run_priority(scenario, policy)
        runs.append({"scenario": index, "calendar": calendar, **_execution(simulation)})
    return runs


def _robust(body, instance, scenarios):
    robust = optimize_priority(instance, scenarios, max_policies=body.max_policies)
    result = {"method": "robust", "robust": robust, "metrics": {},
              "information": robust["guarantee"]}
    if robust["policy"] is None:
        result["execution_status"] = ("infeasible" if robust["enumeration_complete"]
                                      else "budget_exhausted")
        return result
    runs = _scenario_runs(instance, robust["policy"], scenarios)
    result.update({key: runs[0][key] for key in ("execution_status", "metrics", "trace")},
                  scenario_runs=runs,
                  search_budget_exhausted=not robust["enumeration_complete"])
    if any(run["execution_status"] != "valid" for run in runs):
        result["execution_status"] = "invalid"
    if body.compare:
        nominal = optimize_priority(instance, [[]], max_policies=body.max_policies)
        comparison = [{"method": "robust", "execution_status": result["execution_status"],
                       "metrics": result["metrics"] | {"total_seconds": robust["total_seconds"]},
                       "scenario_runs": runs,
                       "policy": robust["policy"], "search_status": robust["status"]}]
        baseline = {"method": "nominal_fixed_queues", "policy": nominal["policy"],
                    "search_status": nominal["status"], "metrics": {},
                    "execution_status": "infeasible" if nominal["enumeration_complete"]
                    else "budget_exhausted"}
        if nominal["policy"] is not None:
            baseline_runs = _scenario_runs(instance, nominal["policy"], scenarios)
            baseline.update(scenario_runs=baseline_runs,
                            metrics=baseline_runs[0]["metrics"] | {"total_seconds": nominal["total_seconds"]},
                            execution_status="valid" if all(run["execution_status"] == "valid"
                                                           for run in baseline_runs) else "incomplete")
        comparison.append(baseline)
        result["comparison"] = comparison
        result["comparison_information"] = (
            "Identical declared calendars; fixed queues selected on the nominal calendar "
            "versus minimax fixed queues. Enumeration budgets and incomplete runs remain explicit.")
    return result


def run_m7(body, instance, scenarios):
    """Execute and check schedules through the V1 engine, retaining scientific statuses."""
    methods = {"nominal": _nominal, "rolling": _rolling}
    if body.method == "robust":
        result = _robust(body, instance, scenarios)
    else:
        result = methods[body.method](body, instance)
        if body.compare:
            other = "rolling" if body.method == "nominal" else "nominal"
            baseline = methods[other](body, instance)
            result["comparison"] = []
            for row in (result, baseline):
                source = row.get("plan", row.get("rolling", {}))
                comparison = {key: row[key] for key in ("method", "execution_status", "metrics", "information")}
                comparison["metrics"] = row["metrics"] | {
                    key: source[key] for key in ("replans", "fallback_dispatches", "solver_seconds", "total_seconds")
                    if key in source}
                result["comparison"].append(comparison)
            result["comparison_information"] = (
                "Same instance and horizon. Nominal is an offline oracle with future tasks "
                "and outages; rolling uses revealed tasks and current availability only.")
    return {"milestone": "m7", **result}


def _mapf_plan(body, problem, method):
    plan = solve(problem, method, priorities=body.priorities,
                 max_nodes=body.max_nodes, seconds=body.seconds)
    status = {
        "SOLVED": "valid", "NO_SOLUTION_WITHIN_HORIZON": "infeasible",
        "BUDGET_EXHAUSTED": "budget_exhausted", "PRIORITY_FAILED": "priority_failed",
    }[plan["status"]]
    return {"method": method, "execution_status": status, "plan": plan,
            "metrics": plan["validation"]["metrics"] if plan["validation"] else {}}


def run_m9(body, problem):
    """Keep MAPF paths and independent collision validation separate from V1 traces."""
    result = {"milestone": "m9", "problem": problem.to_dict(),
              **_mapf_plan(body, problem, body.method)}
    if body.compare:
        other = "cbs" if body.method == "prioritized" else "prioritized"
        result["comparison"] = [
            {key: result[key] for key in ("method", "execution_status", "plan", "metrics")},
            _mapf_plan(body, problem, other),
        ]
        result["comparison_information"] = "Same MAPF problem and search budgets for each algorithm."
    return result
