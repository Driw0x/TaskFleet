"""Reproduce M7 comparisons, including failures and out-of-set calendars."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.metrics import metrics
from taskfleet.scheduling import (execute_schedule, optimize_priority, run_priority,
                                  run_rolling, solve_schedule)


def run_benchmark(*, budget=1.0, windows=(8, 18), frequencies=(1, 4), max_policies=10000):
    started = perf_counter()
    records, cases = [], []
    for name, filename in [('stations', 'examples/m7.json'), ('dynamic', 'examples/dynamic.json'),
                           ('robust_tradeoff', 'examples/m7_robust.json')]:
        original = load_instance(filename)
        nominal_data = original.to_dict()
        nominal_data['unavailability'] = []
        nominal = from_dict(nominal_data)
        inside = [[], original.unavailability,
                  [dict(robot_id=sorted(original.robots)[1], start_tick=1, end_tick=4)]]
        outside = [dict(robot_id=sorted(original.robots)[0], start_tick=1, end_tick=original.horizon - 1)]
        calendars = [('nominal', inside[0]), ('declared_a', inside[1]),
                     ('declared_b', inside[2]), ('outside_set', outside)]
        schedule = solve_schedule(nominal, budget=budget)
        witness = optimize_priority(nominal, [[]], max_policies=max_policies)
        robust = optimize_priority(nominal, inside, max_policies=max_policies)
        cases.append(dict(name=name, instance=original.to_dict(), uncertainty_set=inside,
                          outside_set=outside, nominal_schedule=schedule,
                          nominal_same_class=witness, robust=robust))
        for scenario, calendar in calendars:
            data = deepcopy(nominal_data)
            data['unavailability'] = deepcopy(calendar)
            instance = from_dict(data)
            def record(method, simulation, elapsed, **extra):
                records.append(dict(case=name, scenario=scenario, method=method,
                                    metrics=metrics(simulation), execution_seconds=elapsed, **extra))
            for method in ['fifo', 'cpsat', 'cnp']:
                before = perf_counter()
                simulation = Simulation(instance, method, solver_budget=budget)
                simulation.run()
                record(method, simulation, perf_counter() - before,
                       optimization_seconds=0.0, total_seconds=perf_counter() - before)
            before = perf_counter()
            simulation, validation = execute_schedule(instance, schedule)
            elapsed = perf_counter() - before
            record('nominal_schedule', simulation, elapsed, validation=validation,
                   optimization_seconds=schedule['total_seconds'],
                   total_seconds=schedule['total_seconds'] + elapsed,
                   plan_status=schedule['status'], plan_optimal=schedule['optimal'])
            for method, selected in [('priority_nominal', witness), ('robust', robust)]:
                if selected['policy'] is None:
                    records.append(dict(case=name, scenario=scenario, method=method,
                                        failure=selected['status'], metrics=None))
                    continue
                before = perf_counter()
                simulation = run_priority(instance, selected['policy'])
                elapsed = perf_counter() - before
                record(method, simulation, elapsed, optimization_seconds=selected['total_seconds'],
                       total_seconds=selected['total_seconds'] + elapsed,
                       policy_status=selected['status'], policy_optimal=selected['optimal'])
            for window in windows:
                for frequency in frequencies:
                    before = perf_counter()
                    simulation, planning = run_rolling(instance, window=window,
                                                       frequency=frequency, budget=budget)
                    elapsed = perf_counter() - before
                    record(f'rolling_{window}_{frequency}', simulation, elapsed,
                        window=window, frequency=frequency, replans=planning['replans'],
                        fallback_dispatches=planning['fallback_dispatches'],
                        dispatch_plan_deviations=planning['dispatch_plan_deviations'],
                        solver_seconds=planning['solver_seconds'], total_seconds=elapsed,
                        plan_statuses=[p['status'] for p in planning['plans']])
    # Deliberately impossible horizon: retain proof/failure rather than drop row.
    impossible = load_instance('examples/m7.json').to_dict()
    impossible['horizon'] = 1
    failure = solve_schedule(from_dict(impossible), budget=budget)
    return dict(milestone='M7', physics='unchanged V1: BFS, no collisions, ID station priority, delayed release',
        protocol=dict(budget_per_phase=budget, budget_unit='deterministic_time', workers=1, solver_seed=0,
                      windows=list(windows), frequencies=list(frequencies), max_policies=max_policies,
                      objectives=['total_tardiness', 'makespan'],
                      nominal_information='all task definitions/releases known offline; no future outages',
                      rolling_information='revealed state only; optimistic future availability',
                      robust_class='fixed assignment and per-robot queues; causal dispatch after revelation',
                      robust_objective='max scenario tardiness, then max scenario makespan; every scenario must complete',
                      timing='model generation, both solves, replanning, enumeration and execution included; policy selection reused across scenario replays'),
        cases=cases, records=records, impossible_horizon=failure,
        total_seconds=perf_counter() - started,
        limits=['time-indexed tables target small horizons; search can return UNKNOWN',
                'short rolling windows fall back to FIFO without discarding backlog',
                'robust guarantee only finite declared calendars and exhaustively evaluated fixed queues',
                'outside-set results have no guarantee; incomplete makespan remains null',
                'nominal schedule knows future task releases, rolling does not; same-class nominal witness isolates robust objective'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget', type=float, default=1.0)
    parser.add_argument('--windows', type=int, nargs='+', default=[8, 18])
    parser.add_argument('--frequencies', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--max-policies', type=int, default=10000)
    parser.add_argument('--json', type=Path, default=Path('docs/results/v2/m7.json'))
    args = parser.parse_args(argv)
    report = run_benchmark(budget=args.budget, windows=tuple(args.windows),
                           frequencies=tuple(args.frequencies), max_policies=args.max_policies)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(records=len(report['records']), cases=len(report['cases']),
                         total_seconds=report['total_seconds'], output=str(args.json)), indent=2))


if __name__ == '__main__':
    main()
