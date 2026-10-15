"""M7 independent dispatch oracle and exact engine replay checks."""

from copy import deepcopy
import json

import pytest

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.metrics import metrics
from taskfleet.models import TaskState
from taskfleet.scheduling import (execute_schedule, main, optimize_priority, plan_schedule,
    run_priority, run_rolling, solve_schedule, temporal_observation)


def micro(*, horizon=9, outage=False, capacity=1):
    return from_dict(dict(width=2, height=1, horizon=horizon,
        robots=[dict(id='a', position=[0, 0], capacity=2), dict(id='b', position=[0, 0], capacity=1)],
        tasks=[dict(id='heavy', pickup=[0, 0], destination=[1, 0], load=2, handling_ticks=2, deadline=4),
               dict(id='light', pickup=[0, 0], destination=[0, 0], deadline=3)],
        resources=[dict(id='station', position=[0, 0], capacity=capacity)],
        unavailability=[dict(robot_id='a', start_tick=2, end_tick=4)] if outage else []))


def exhaustive_dispatch(instance):
    """Enumerate actual manual-engine dispatches, including intentional idle."""
    states = [Simulation(instance)]
    best = None
    seen = set()
    while states:
        simulation = states.pop()
        key = json.dumps(simulation.snapshot(), sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        if simulation.all_tasks_completed:
            values = metrics(simulation)
            candidate = (values['total_tardiness'], values['makespan'])
            best = candidate if best is None or candidate < best else best
            continue
        if simulation.finished:
            continue
        choices = [simulation]
        for robot_id in sorted(simulation.robots):
            expanded = []
            for current in choices:
                expanded.append(current)
                for task in current.observation().tasks:
                    branch = deepcopy(current)
                    try:
                        branch.assign(robot_id, task.id)
                    except ValueError:
                        continue
                    expanded.append(branch)
            choices = expanded
        for branch in choices:
            branch.step()
            states.append(branch)
    return best


@pytest.mark.parametrize('outage,capacity', [(False, 1), (True, 1), (False, 2)])
def test_exact_temporal_optimum_against_dispatch_oracle(outage, capacity):
    instance = micro(outage=outage, capacity=capacity)
    plan = solve_schedule(instance)
    assert plan['optimal']
    assert tuple(plan['objective']) == exhaustive_dispatch(instance)
    simulation, validation = execute_schedule(instance, plan)
    assert validation['executable']
    assert all(d['completed_delta'] == d['collected_delta'] == 0 for d in validation['deviations'])
    assert simulation.tasks['heavy'].robot_id == 'a'
    simulation.check_invariants()


def test_suspension_holds_resource_and_release_waits_until_next_tick():
    data = micro(outage=True).to_dict()
    data['tasks'][0]['deadline'] = 0
    data['tasks'][1]['deadline'] = None
    instance = from_dict(data)
    plan = solve_schedule(instance)
    simulation, validation = execute_schedule(instance, plan)
    assert validation['executable']
    assert simulation.snapshots[2]['resources']['station']['holders'] == ['a']
    assert simulation.snapshots[3]['resources']['station']['holders'] == ['a']
    acquired = [e for e in simulation.events if e['kind'] == 'resource_acquired']
    released = [e for e in simulation.events if e['kind'] == 'resource_released']
    for first, second in zip(acquired, acquired[1:]):
        release = next(e for e in released if e['robot_id'] == first['robot_id'] and e['tick'] >= first['tick'])
        assert second['tick'] > release['tick']


def test_coincident_endpoints_two_handlings_and_robot_successor_travel():
    instance = from_dict(dict(width=3, height=1, horizon=12,
        robots=[dict(id='r', position=[0, 0], capacity=1)],
        tasks=[dict(id='x', pickup=[0, 0], destination=[0, 0], handling_ticks=2, deadline=4),
               dict(id='y', pickup=[2, 0], destination=[2, 0], deadline=8)]))
    plan = solve_schedule(instance)
    assert plan['objective'] == [0, 8]
    assert plan['tasks']['x'] == dict(collected_tick=2, completed_tick=4)
    assert plan['tasks']['y'] == dict(collected_tick=7, completed_tick=8)
    assert execute_schedule(instance, plan)[1]['executable']


def test_infeasible_budget_unknown_and_empty():
    assert solve_schedule(micro(horizon=1))['status'] == 'INFEASIBLE'
    unknown = solve_schedule(micro(), budget=1e-9)
    assert unknown['status'] == 'UNKNOWN' and not unknown['optimal'] and unknown['objective'] is None
    empty = from_dict(dict(width=1, height=1, horizon=0, robots=[], tasks=[]))
    assert solve_schedule(empty)['objective'] == [0, 0]
    assert execute_schedule(empty, solve_schedule(empty))[1]['executable']


def test_second_phase_budget_keeps_first_feasible_solution(monkeypatch):
    from ortools.sat.python import cp_model
    original = cp_model.CpSolver.solve
    calls = 0
    def limited(self, model, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, model, *args, **kwargs) if calls == 1 else cp_model.UNKNOWN
    monkeypatch.setattr(cp_model.CpSolver, 'solve', limited)
    plan = solve_schedule(micro())
    assert plan['phases'] == ['OPTIMAL', 'UNKNOWN']
    assert plan['status'] == 'UNKNOWN' and plan['feasible'] and not plan['optimal']
    assert execute_schedule(micro(), plan)[1]['executable']


@pytest.mark.parametrize('budget', [0, -1, True, float('nan')])
def test_invalid_budget(budget):
    with pytest.raises(ValueError, match='budget'):
        solve_schedule(micro(), budget=budget)


def test_rolling_hides_future_and_observation_is_detached():
    data = micro().to_dict()
    data['tasks'][1]['arrival_tick'] = 4
    data['unavailability'] = [dict(robot_id='a', start_tick=3, end_tick=5)]
    alternate = deepcopy(data)
    alternate['tasks'][1]['arrival_tick'] = 7
    alternate['tasks'][1]['deadline'] = 1
    alternate['unavailability'] = [dict(robot_id='a', start_tick=6, end_tick=8)]
    first, second = Simulation(from_dict(data)), Simulation(from_dict(alternate))
    view = temporal_observation(first)
    assert view == temporal_observation(second)
    assert not hasattr(view, 'unavailability') and not hasattr(view, 'configuration')
    assert [t.id for t in view.tasks] == ['heavy']
    view.robots[0].position = (1, 0)
    assert first.robots['a'].position == (0, 0)
    a = plan_schedule(temporal_observation(first), 8)
    b = plan_schedule(temporal_observation(second), 8)
    assert a['assignments'] == b['assignments'] and a['objective'] == b['objective']


def test_rolling_arrivals_outages_frozen_commitments_and_backlog():
    instance = load_instance('examples/dynamic.json')
    simulation, info = run_rolling(instance, window=8, frequency=4, budget=0.1)
    assert simulation.all_tasks_completed
    assert info['replans'] > 1 and info['fallback_dispatches'] > 0
    assigned = [e for e in simulation.events if e['kind'] == 'assigned']
    assert len(assigned) == len(instance.tasks)
    assert all(e['tick'] >= instance.tasks[e['task_id']].arrival_tick for e in assigned)
    assert simulation.snapshots[3]['resources']['pickup']['holders'] == ['r1']
    simulation.check_invariants()
    for plan in info['plans']:
        for action in plan['assignments']:
            assert instance.tasks[action['task_id']].arrival_tick <= plan['start_tick']


def test_robust_same_policy_class_exhaustive_and_outside_set():
    instance = micro()
    scenarios = [[], [dict(robot_id='a', start_tick=2, end_tick=4)]]
    nominal = optimize_priority(instance, [[]])
    robust = optimize_priority(instance, scenarios)
    assert nominal['optimal'] and robust['optimal']
    assert robust['policies_evaluated'] == nominal['policies_evaluated']
    assert robust['objective'] == [6, 7]
    assert all(v['completion_rate'] == 1 for v in robust['scenario_metrics'])
    nominal_worst = []
    for calendar in scenarios:
        data = instance.to_dict()
        data['unavailability'] = calendar
        nominal_worst.append(metrics(run_priority(from_dict(data), nominal['policy']))['total_tardiness'])
    assert robust['objective'][0] <= max(nominal_worst)
    data = instance.to_dict()
    data['unavailability'] = [dict(robot_id='a', start_tick=1, end_tick=9)]
    assert not run_priority(from_dict(data), robust['policy']).all_tasks_completed


def test_robust_budget_infeasible_and_invalid_policies():
    budget = optimize_priority(micro(), [[]], max_policies=1)
    assert not budget['enumeration_complete'] and not budget['optimal']
    failure = optimize_priority(micro(horizon=1), [[]])
    assert failure['status'] == 'INFEASIBLE' and failure['policy'] is None
    with pytest.raises(ValueError, match='scenario'):
        optimize_priority(micro(), [])
    with pytest.raises(ValueError, match='exactly once'):
        run_priority(micro(), dict(a=['heavy', 'light'], b=['light']))
    with pytest.raises(ValueError, match='incompatible'):
        run_priority(micro(), dict(a=['light'], b=['heavy']))


def test_robust_changes_here_and_now_policy_and_improves_declared_worst_case():
    instance = load_instance('examples/m7_robust.json')
    scenarios = [[], instance.unavailability,
                 [dict(robot_id='b', start_tick=1, end_tick=4)]]
    nominal = optimize_priority(instance, [[]])
    robust = optimize_priority(instance, scenarios)
    assert nominal['objective'] == [2, 8]
    assert robust['objective'] == [9, 13]
    assert nominal['policy'] != robust['policy']
    assert nominal['policies_evaluated'] == robust['policies_evaluated'] == 24
    nominal_worst = 0
    for scenario in scenarios:
        data = instance.to_dict()
        data['unavailability'] = scenario
        nominal_worst = max(nominal_worst, metrics(run_priority(from_dict(data), nominal['policy']))['total_tardiness'])
    assert nominal_worst == 11 > robust['objective'][0]


def test_schedule_repeat_and_cli(tmp_path, capsys):
    instance = micro()
    first, second = solve_schedule(instance), solve_schedule(instance)
    assert first['assignments'] == second['assignments']
    assert first['tasks'] == second['tasks']
    output = tmp_path / 'm7.json'
    assert main(['examples/simple.json', '--json', str(output)]) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['validation']['executable']
    capsys.readouterr()


def test_replay_reports_perturbation_deviations():
    instance = micro()
    plan = solve_schedule(instance)
    data = instance.to_dict()
    data['unavailability'] = [dict(robot_id='a', start_tick=2, end_tick=4)]
    _, check = execute_schedule(from_dict(data), plan)
    assert not check['executable']
    assert any(d['completed_delta'] != 0 for d in check['deviations'])


def test_rolling_and_priority_cli_exports(tmp_path, capsys):
    for method in ['rolling', 'priority', 'robust']:
        output = tmp_path / f'{method}.json'
        arguments = ['examples/m7.json', '--method', method, '--json', str(output)]
        if method == 'robust':
            arguments += ['--scenarios', 'examples/m7/scenarios.json']
        assert main(arguments) == 0
        result = json.loads(output.read_text(encoding='utf-8'))
        assert result['metrics']['completion_rate'] == 1.0
        assert not result['metrics']['invalid_decisions']
    capsys.readouterr()
