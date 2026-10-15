"""Bounded local-model comparison: calibration, validation selection, held-out test.

Fixed local-observation replay measures identical inputs across candidates.
Separate real M8/M10 rollouts measure allocation/episode quality; their states
may diverge. Only validation selects models. No model installation is performed.
"""

import argparse
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
from statistics import mean, median
from time import perf_counter

from taskfleet import Simulation, load_instance
from taskfleet.benchmark import instance_digest
from taskfleet.dcop import compare_snapshot, distributed, local_agents
from taskfleet.instances import generate_instance
from taskfleet.marl import LLM_CONTEXT, QPolicy, run_episode
from taskfleet.metrics import metrics
from taskfleet.ollama import OllamaClient, OllamaConfig


DEFAULT_OUTPUT = Path('docs/results/v2/ollama-selection-20261015')
DEFAULT_CHECKPOINT = Path('docs/results/v2/m10-q-seed-0.json')
M8_CONTEXT = ('Maximize distinct assigned tasks, then minimize own cost. '
              'Neighbors selecting your task create a conflict. Idle is null.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def physical_digest(instance):
    value = instance.to_dict()
    value.pop('seed', None)
    return digest(value)


def build_splits():
    """Physical configurations, not just seed labels, must differ across splits."""
    result = {'calibration': [load_instance('examples/allocation.json')]}
    for split, seeds in [('validation', [411, 413]), ('test', [421, 423])]:
        result[split] = []
        for seed in seeds:
            instance = generate_instance(width=4, height=3, robot_count=2,
                                         task_count=3, seed=seed, horizon=30)
            for index, task in enumerate(instance.tasks.values()):
                task.deadline = 8 + 4 * index
            result[split].append(instance)
    hashes = [physical_digest(instance) for values in result.values() for instance in values]
    if len(set(hashes)) != len(hashes):
        raise ValueError('calibration, validation and test must be physically disjoint')
    return result


def fixed_observations(instance, usage, *, rounds=2, probability=0.7, seed=11, limit=8):
    """Replay corpus comes from unchanged DSA and heuristic reference trajectories."""
    result = []
    if usage == 'm8':
        observation = Simulation(instance).observation()
        reference = distributed(observation, rounds=rounds, probability=probability, seed=seed)
        agents = local_agents(observation)
        for number in range(1, reference['rounds'] + 1):
            for agent in agents:
                agent.neighbors = tuple(reference['neighbors'][agent.robot.id])
                agent.value = (agent.initial_value() if number == 1 else
                               reference['history'][number - 2]['values'][agent.robot.id])
                agent.received = {message['sender']: message['action'] for message in reference['messages']
                                 if message['type'] == 'value' and message['round'] == number
                                 and message['recipient'] == agent.robot.id}
                result.append(dict(observation=agent.llm_observation(),
                                   allowed_actions=list(agent.domain), context=M8_CONTEXT))
    elif usage == 'm10':
        reference = run_episode(instance, 'heuristic', seed=seed, trace=True)
        for decision in reference['decisions']:
            for observation in decision['observations'].values():
                if len(observation['legal_actions']) > 1:
                    result.append(dict(observation=observation,
                                       allowed_actions=observation['legal_actions'],
                                       context=LLM_CONTEXT))
    else:
        raise ValueError('usage must be m8 or m10')
    return deepcopy(result[:limit])


def replay_observations(items, client):
    started = perf_counter()
    for item in items:
        client.decide(deepcopy(item['observation']), deepcopy(item['allowed_actions']), context=item['context'])
    return dict(observations_sha256=digest(items), decisions=len(items),
                llm_metrics=deepcopy(client.metrics), llm_trace=deepcopy(client.trace),
                total_seconds=perf_counter() - started)


def _classical_episode(instance, method, seed, policy):
    if method in {'heuristic', 'random', 'q'}:
        return run_episode(instance, method, policy=policy, seed=seed, trace=True)
    started = perf_counter()
    simulation = Simulation(instance, method)
    while not simulation.finished and not simulation.all_tasks_completed:
        simulation.step()
    reward = -sum(sum(t['arrival_tick'] <= snapshot['tick'] and t['state'] != 'completed'
                      for t in snapshot['tasks'].values()) for snapshot in simulation.snapshots[1:])
    return dict(**metrics(simulation), policy=method, seed=seed, **{'return': reward},
                total_seconds=perf_counter() - started, trace=simulation.trace())


def _applied_nonidle(usage, result):
    traces = result.get('llm_trace', [])
    generated = {(row['observation']['robot']['id'], row['action'], row['observation'].get('tick'))
                 for row in traces if not row.get('fallback', True) and row.get('action') is not None}
    if usage == 'm8':
        return sum(any(robot == r and action == t for r, t, _ in generated)
                   for robot, action in result.get('assignments', []))
    return sum((robot, task, decision['tick']) in generated for decision in result.get('decisions', [])
               for robot, task in decision['accepted'])


def select_models(report, configurations):
    """Hard eligibility, then explicit lexicographic criteria; never inspect test."""
    summaries, selected = {}, {}
    expected = len(report['splits']['validation']) * report['protocol']['repeats']
    all_complete = all(row.get('result', {}).get('completion_rate') == 1 for row in report['runs']
                       if row['split'] == 'validation' and row.get('model') and row['usage'] == 'm10')
    for usage in ['m8', 'm10']:
        rows = []
        for model, config in configurations.items():
            rollouts = [r for r in report['runs'] if r['split'] == 'validation'
                        and r.get('model') == model and r['usage'] == usage]
            replays = [r for r in report['replay_runs'] if r['split'] == 'validation'
                       and r.get('model') == model and r['usage'] == usage]
            reasons = []
            if len(rollouts) != expected or len(replays) != expected:
                reasons.append('missing_validation_runs')
            if any('error' in row for row in [*rollouts, *replays]):
                reasons.append('validation_run_failed')
            successful = [r['result'] for r in rollouts if 'result' in r]
            traces = [trace for r in rollouts + replays for trace in r.get('result', {}).get('llm_trace', [])]
            decisions = len(traces)
            genuine = sum(not trace.get('fallback', True) for trace in traces)
            structured = sum(bool(trace.get('structured_valid', not trace.get('fallback', True))) for trace in traces)
            legal = sum(bool(trace.get('action_legal', not trace.get('fallback', True))) for trace in traces)
            if not decisions or genuine != decisions or structured != decisions or legal != decisions:
                reasons.append('validation_requires_100_percent_valid_generations_without_fallback')
            applied = sum(_applied_nonidle(usage, value) for value in successful)
            if applied < 1:
                reasons.append('no_genuine_nonidle_applied_action')
            if usage == 'm8':
                if any(not value['final_quality']['feasible'] for value in successful):
                    reasons.append('infeasible_allocation')
                quality = [-sum(value['final_quality']['cardinality'] for value in successful),
                           sum(value['final_quality']['estimated_cost'] for value in successful),
                           sum(value['raw_quality']['conflict_pairs'] for value in successful),
                           sum(value['transmissions'] for value in successful)]
                quality_order = ['negative_cardinality', 'cost_at_equal_cardinality', 'raw_conflicts', 'transmissions']
            else:
                if any(value.get('rejected_actions', 0) or value['invalid_decisions'] for value in successful):
                    reasons.append('illegal_engine_action')
                quality = [-sum(value['completion_rate'] == 1 for value in successful),
                           -sum(value['completion_rate'] for value in successful),
                           -sum(value['return'] for value in successful),
                           sum(value['total_tardiness'] + value['unfinished_tardiness_lower_bound'] for value in successful)]
                quality_order = ['negative_complete_runs', 'negative_completion_rate', 'negative_return',
                                 'tardiness_plus_unfinished_lower_bound']
                if all_complete:
                    quality.append(sum(value['makespan'] for value in successful))
                    quality_order.append('makespan_all_validation_runs_complete')
                quality.extend([sum(value['conflicts'] for value in successful),
                                sum(value['distance'] for value in successful)])
                quality_order.extend(['conflicts', 'distance'])
            replay_traces = [trace for r in replays for trace in r.get('result', {}).get('llm_trace', [])]
            latency = median([t['latency_seconds'] for t in replay_traces]) if replay_traces else None
            info = next((m for m in report['inventory'].get('models', []) if m['name'] == model), {})
            rank = [*quality, latency, info.get('size_bytes') or 0, model]
            rows.append(dict(model=model, eligible=not reasons, reasons=reasons,
                             validation_rank=rank, quality_order=quality_order,
                             decisions=decisions, genuine_decisions=genuine, structured=structured, legal=legal,
                             genuine_applied_nonidle=applied, median_latency_seconds=latency))
        rows.sort(key=lambda row: (not row['eligible'], row['validation_rank'] if row['eligible'] else [row['model']]))
        summaries[usage] = rows
        winner = next((r for r in rows if r['eligible']), None)
        model = winner['model'] if winner else None
        info = next((m for m in report['inventory'].get('models', []) if m['name'] == model), {})
        selected[usage] = dict(model=model, config=asdict(configurations[model]) if model else None,
                               model_digest=info.get('digest') if model else None,
                               validation_rank=winner['validation_rank'] if winner else None, validation_only=True)
    return summaries, selected


def summarize_records(records):
    """Descriptive paired aggregates; replay and rollout records stay separate."""
    groups = {}
    for row in records:
        groups.setdefault((row['split'], row['usage'], row['method'], row.get('model')), []).append(row)
    summaries = []
    for (split, usage, method, model), rows in groups.items():
        values = [row['result'] for row in rows if 'result' in row]
        llm = [value.get('llm_metrics', value.get('llm', {})) for value in values]
        traces = [trace for value in values for trace in value.get('llm_trace', [])]
        measured = sum(value.get('token_measurements', 0) for value in llm)
        summary = dict(split=split, usage=usage, method=method, model=model, runs=len(rows),
            failed_runs=len(rows) - len(values), calls=sum(value.get('calls', 0) for value in llm),
            genuine_decisions=sum(not trace.get('fallback', True) for trace in traces),
            structured_responses=sum(bool(trace.get('structured_valid')) for trace in traces),
            legal_actions=sum(bool(trace.get('action_legal')) for trace in traces),
            decisions=len(traces), fallbacks=sum(value.get('fallbacks', 0) for value in llm),
            timeouts=sum(value.get('errors', {}).get('timeout', 0) for value in llm),
            errors={error: sum(value.get('errors', {}).get(error, 0) for value in llm)
                    for error in sorted({key for value in llm for key in value.get('errors', {})})},
            token_measurements=measured,
            prompt_tokens_reported=sum(value.get('prompt_tokens', 0) for value in llm) if measured else None,
            generated_tokens_reported=sum(value.get('generated_tokens', 0) for value in llm) if measured else None,
            median_decision_latency_seconds=median([trace['latency_seconds'] for trace in traces]) if traces else None,
            genuine_applied_nonidle=sum(_applied_nonidle(usage, value) for value in values)
                                    if method == 'llm' else None,
            total_seconds=sum(value.get('total_seconds', 0) for value in values), quality={})
        if traces:
            summary['structured_response_rate'] = summary['structured_responses'] / len(traces)
            summary['legal_action_rate'] = summary['legal_actions'] / len(traces)
        for key in (['cardinality', 'estimated_cost', 'raw_conflict_pairs', 'logical_messages', 'transmissions', 'rounds']
                    if usage == 'm8' else ['completion_rate', 'return', 'makespan', 'distance', 'total_tardiness',
                                          'unfinished_tardiness_lower_bound', 'conflicts', 'rejected_actions', 'invalid_decisions']):
            samples = []
            for value in values:
                quality = value.get('final_quality', value.get('quality', {}))
                raw = value.get('raw_quality', quality)
                sample = (raw.get('conflict_pairs') if key == 'raw_conflict_pairs' else
                          value.get(key, quality.get(key)))
                if sample is not None:
                    samples.append(sample)
            summary['quality'][key] = dict(count=len(samples), mean=mean(samples) if samples else None,
                                           total=sum(samples) if samples else None)
        if usage == 'm8':
            summary['feasible_runs'] = sum(value.get('final_quality', value.get('quality', {})).get('feasible', False)
                                           for value in values)
        else:
            summary['complete_runs'] = sum(value.get('completion_rate') == 1 for value in values)
        summaries.append(summary)
    return summaries


def benchmark(*, output=DEFAULT_OUTPUT, models=None, url='http://localhost:11434',
              timeout=30.0, load_timeout=120.0, scenario_budget=120.0, call_budget=8,
              max_tokens=64, temperature=0.0, num_ctx=2048, seed=11, rounds=2,
              probability=0.7, repeats=2, cold=False, keep_alive='5m',
              checkpoint=DEFAULT_CHECKPOINT, think=False):
    from taskfleet.ollama import discover_models, inspect_runtime, preload_model, probe_model, unload_model

    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be a positive integer')
    if type(rounds) is not int or rounds < 1:
        raise ValueError('rounds must be a positive integer')
    if call_budget < 1:
        raise ValueError('call_budget must be positive for model comparison')
    common = OllamaConfig(url=url, timeout=timeout, load_timeout=load_timeout,
                          scenario_budget=scenario_budget, call_budget=call_budget,
                          max_tokens=max_tokens, temperature=temperature, num_ctx=num_ctx,
                          seed=seed, keep_alive=keep_alive, think=think)
    policy = QPolicy.load(checkpoint)
    frozen_policy = deepcopy(policy.to_dict())
    splits = build_splits()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / 'report.json'
    started = perf_counter()
    report = dict(schema=1, version=1, status='running',
        protocol=dict(config=asdict(common), repeats=repeats, rounds=rounds, probability=probability,
            seed=seed, cold=cold, split_seeds={'calibration': ['examples/allocation.json'],
            'validation': [411, 413], 'test': [421, 423]},
            selection='validation only; hard validity/reliability eligibility; lexicographic quality, latency, disk size',
            eligibility='100% structured/legal decisions, no fallback, >=1 genuine nonidle applied action',
            replay='identical local observations from unchanged DSA/heuristic reference trajectories',
            rollout='unchanged distributed/run_episode; model-dependent states may diverge',
            test='union of frozen selected models evaluated on both usages; no reselection',
            cold_measurement='explicit unload before preload only with --cold; preload then warm simple probe',
            prompts={'m8': M8_CONTEXT, 'm10': LLM_CONTEXT},
            q_checkpoint=str(checkpoint), q_checkpoint_sha256=hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()),
        environment=dict(python=platform.python_version(), ortools=version('ortools')),
        splits={split: [dict(instance=instance.to_dict(), instance_sha256=instance_digest(instance),
                            physical_sha256=physical_digest(instance)) for instance in instances]
                for split, instances in splits.items()},
        inventory={}, calibration=[], fixed_observations={}, replay_runs=[], runs=[],
        validation_summary={}, selection={}, exclusions=[], errors=[])
    report['protocol_sha256'] = digest(report['protocol'])
    def save():
        report['elapsed_seconds'] = perf_counter() - started
        report['comparisons'] = summarize_records(report['runs'])
        report['replay_comparisons'] = summarize_records(report['replay_runs'])
        temporary = output / 'report.tmp'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(report_path)
    def attempt(stage, function, **details):
        try:
            return function()
        except (OSError, ValueError, RuntimeError) as error:
            failure = dict(stage=stage, error=type(error).__name__, error_detail=str(error), **details)
            report['errors'].append(failure)
            save()
            return failure
    def add_run(split, index, repetition, usage, method, result, model=None, replay=False):
        row = dict(split=split, index=index, repetition=repetition, usage=usage, method=method,
                   model=model, instance_sha256=instance_digest(splits[split][index]))
        row.update(result=result) if not ('error' in result and 'stage' in result) else row.update(error=result)
        if model is not None and 'result' in row:
            row['temperature_condition'] = 'preload_and_probe_succeeded; subsequent_reload_not_excluded'
            row['reported_load_durations_seconds'] = [trace['timings_seconds']['load_duration']
                for trace in result.get('llm_trace', []) if 'load_duration' in trace.get('timings_seconds', {})]
        report['replay_runs' if replay else 'runs'].append(row)
        save()
    def evaluate_model(model, split):
        config = configurations[model]
        for index, instance in enumerate(splits[split]):
            for repetition in range(repeats):
                for usage in ['m8', 'm10']:
                    client = OllamaClient(config)
                    items = corpus[split, index, usage]
                    result = attempt('fixed_replay', lambda: replay_observations(items, client),
                                     model=model, split=split, usage=usage, index=index, repetition=repetition)
                    add_run(split, index, repetition, usage, 'llm_replay', result, model, replay=True)
                    client = OllamaClient(config)
                    result = attempt('rollout', lambda: (
                        distributed(Simulation(instance).observation(), rounds=rounds, probability=probability,
                                    seed=seed, llm=client) if usage == 'm8' else
                        run_episode(instance, 'llm', client=client, seed=seed, trace=True)),
                        model=model, split=split, usage=usage, index=index, repetition=repetition)
                    add_run(split, index, repetition, usage, 'llm', result, model)
    def preparation_failure(model, split, entry):
        for index in range(len(splits[split])):
            for repetition in range(repeats):
                for usage in ['m8', 'm10']:
                    failure = dict(stage='preparation', error='preparation_failed',
                                   error_detail='preload or structured warm probe did not succeed',
                                   preload_status=entry['preload'].get('status'),
                                   probe_decision=entry.get('probe', {}).get('decision'))
                    add_run(split, index, repetition, usage, 'llm_replay', failure, model, replay=True)
                    add_run(split, index, repetition, usage, 'llm', failure, model)
    def prepare(model, phase):
        config = configurations[model]
        entry = dict(model=model, phase=phase, config=asdict(config),
                     residency_before=attempt('runtime_before', lambda: inspect_runtime(url, min(timeout, 10))))
        report['calibration'].append(entry)
        save()
        for running in entry['residency_before'].get('running_models', []):
            other = running.get('name', running.get('model'))
            if other != model and other in configurations:
                entry.setdefault('unloaded_other_candidates', []).append(
                    attempt('unload_other', lambda: unload_model(configurations[other]), model=other))
        if cold:
            entry['cold_unload'] = attempt('cold_unload', lambda: unload_model(config), model=model)
            save()
        entry['preload'] = attempt('preload', lambda: preload_model(config), model=model)
        save()
        entry['probe'] = (attempt('warm_probe', lambda: probe_model(config), model=model)
                          if entry['preload'].get('status') == 'ok' else
                          dict(skipped=True, reason='preload_failed'))
        entry['residency_after_preload'] = attempt('runtime_after_preload', lambda: inspect_runtime(url, min(timeout, 10)))
        entry['preparation_ok'] = (entry['preload'].get('status') == 'ok'
                                   and entry['probe'].get('decision', {}).get('fallback') is False)
        resident = entry['residency_after_preload'].get('running_models', [])
        entry['residency_confirmed'] = any(model in [item.get('name'), item.get('model')] for item in resident)
        entry['measurement_condition'] = ('warm_preloaded_resident' if entry['residency_confirmed']
                                           else 'residency_uncertain') if entry['preparation_ok'] else 'preparation_failed'
        entry['reload_diagnostic'] = 'Per-call load_duration is retained; positive duration alone does not prove an eviction/reload.'
        save()
        return entry
    save()
    try:
        report['inventory'] = attempt('inventory', lambda: discover_models(url, min(timeout, 10)))
        save()
        available = {m['name']: m for m in report['inventory'].get('models', [])}
        candidates = list(dict.fromkeys(models if models is not None else sorted(available)))
        configurations = {}
        for model in candidates:
            info = available.get(model)
            if info is None:
                report['exclusions'].append(dict(model=model, reason='not_installed',
                                                  install_command=f'ollama pull {model}', download_performed=False))
            elif 'cloud' in model.lower() or info.get('show', {}).get('remote_model') or info.get('show', {}).get('remote_host'):
                report['exclusions'].append(dict(model=model, reason='cloud_not_authorized'))
            else:
                configurations[model] = replace(common, model=model)
        report['candidates'] = list(configurations)
        save()
        corpus = {}
        for split, instances in splits.items():
            report['fixed_observations'][split] = []
            for index, instance in enumerate(instances):
                item = dict(index=index, instance_sha256=instance_digest(instance), usages={})
                for usage in ['m8', 'm10']:
                    items = fixed_observations(instance, usage, rounds=rounds, probability=probability,
                                               seed=seed, limit=call_budget)
                    corpus[split, index, usage] = items
                    item['usages'][usage] = dict(observations_sha256=digest(items), items=items)
                report['fixed_observations'][split].append(item)
        report['protocol']['fixed_corpus_sha256'] = digest(report['fixed_observations'])
        report['protocol_sha256'] = digest(report['protocol'])
        save()
        for split in ['validation', 'test']:
            for index, instance in enumerate(splits[split]):
                for repetition in range(repeats):
                    comparison = compare_snapshot(Simulation(instance).observation(), rounds=rounds,
                                                   probability=probability, seed=seed)
                    for method in ['dsa', 'cnp', 'cpsat', 'exhaustive']:
                        add_run(split, index, repetition, 'm8', method, comparison['methods'][method])
                    for method in ['heuristic', 'q', 'random', 'fifo', 'cpsat', 'cnp']:
                        add_run(split, index, repetition, 'm10', method,
                                _classical_episode(instance, method, seed, policy))
        for model in configurations:
            entry = prepare(model, 'calibration_validation')
            if entry['preparation_ok']:
                for index in range(len(splits['calibration'])):
                    for usage in ['m8', 'm10']:
                        result = attempt('calibration_replay', lambda: replay_observations(
                            corpus['calibration', index, usage], OllamaClient(configurations[model])), model=model, usage=usage)
                        add_run('calibration', index, 0, usage, 'llm_replay', result, model, replay=True)
                evaluate_model(model, 'validation')
            else:
                preparation_failure(model, 'validation', entry)
            entry['residency_after_runs'] = attempt('runtime_after_runs', lambda: inspect_runtime(url, min(timeout, 10)))
            entry['unload'] = attempt('unload_after_runs', lambda: unload_model(configurations[model]), model=model)
            save()
        report['validation_summary'], report['selection'] = select_models(report, configurations)
        report['selection_sha256'] = digest(report['selection'])
        report['status'] = 'selection_frozen'
        save()
        for model in dict.fromkeys(value['model'] for value in report['selection'].values() if value['model']):
            entry = prepare(model, 'held_out_test')
            if entry['preparation_ok']:
                evaluate_model(model, 'test')
            else:
                preparation_failure(model, 'test', entry)
            entry['residency_after_runs'] = attempt('runtime_after_test', lambda: inspect_runtime(url, min(timeout, 10)))
            entry['unload'] = attempt('unload_after_test', lambda: unload_model(configurations[model]), model=model)
            save()
        assert frozen_policy == policy.to_dict(), 'the Q checkpoint must remain frozen'
        assert report['selection_sha256'] == digest(report['selection']), 'test must not change selection'
        report['status'] = 'complete'
        save()
    except BaseException as error:
        report['status'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
        report['errors'].append(dict(stage='benchmark', error=type(error).__name__, error_detail=str(error)))
        save()
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--url', default='http://localhost:11434')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--load-timeout', type=float, default=120)
    parser.add_argument('--scenario-budget', type=float, default=120)
    parser.add_argument('--call-budget', type=int, default=8)
    parser.add_argument('--max-tokens', type=int, default=64)
    parser.add_argument('--temperature', type=float, default=0)
    parser.add_argument('--num-ctx', type=int, default=2048)
    parser.add_argument('--seed', type=int, default=11)
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--probability', type=float, default=0.7)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--cold', action='store_true')
    parser.add_argument('--keep-alive', default='5m')
    parser.add_argument('--think', choices=['true', 'false'], default='false')
    parser.add_argument('--checkpoint', type=Path, default=DEFAULT_CHECKPOINT)
    args = vars(parser.parse_args(argv))
    args['think'] = args['think'] == 'true'
    try:
        report = benchmark(**args)
        print(json.dumps(dict(status=report['status'], selection=report['selection'],
                              report=str(Path(args['output']) / 'report.json')), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'Error: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
