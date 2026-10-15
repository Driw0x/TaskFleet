"""The complete selection protocol runs against a mocked Ollama HTTP boundary."""

from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from scripts import benchmark_ollama as experiment
from taskfleet import ollama
from taskfleet.ollama import OllamaConfig


@pytest.fixture
def server(monkeypatch):
    requests, resident = [], []
    def request(url, timeout, payload=None):
        requests.append((url, timeout, deepcopy(payload)))
        endpoint = url.rsplit('/', 1)[-1]
        if endpoint == 'version':
            return {'version': 'mock-1'}
        if endpoint == 'tags':
            return {'models': [dict(name=name, digest='digest-' + name, size=size)
                               for name, size in [('good:small', 1), ('bad:large', 2)]]}
        if endpoint == 'show':
            return {'capabilities': ['completion'], 'model_info': {'general.architecture': 'mock',
                    'general.parameter_count': 1, 'mock.context_length': 2048}}
        if endpoint == 'ps':
            return {'models': [dict(name=name, size_vram=1) for name in resident]}
        if endpoint == 'generate':
            if payload['keep_alive'] == 0:
                if payload['model'] in resident:
                    resident.remove(payload['model'])
            elif payload['model'] not in resident:
                resident.append(payload['model'])
            return {'done': True, 'load_duration': 1_000_000, 'total_duration': 2_000_000}
        assert endpoint == 'chat', 'no pull/delete/cloud endpoint is authorized'
        observation = json.loads(payload['messages'][1]['content'])['observation']
        if payload['model'] == 'bad:large' and 'probe' not in observation:
            return {'message': {'content': 'invalid JSON'}}
        legal = payload['format']['properties']['action']['enum']
        costs = observation.get('own_costs', observation.get('costs', {}))
        choices = [a for a in legal if a is not None]
        action = min(choices, key=lambda task: (costs.get(task, 0), task), default=None)
        return {'message': {'content': json.dumps({'action': action})},
                'prompt_eval_count': 30, 'eval_count': 6, 'load_duration': 0,
                'prompt_eval_duration': 1_000_000, 'eval_duration': 2_000_000,
                'total_duration': 3_000_000}
    monkeypatch.setattr(ollama, '_request', request)
    return requests


def test_splits_and_local_replay_are_deterministic_physically_disjoint():
    splits = experiment.build_splits()
    physical = [experiment.physical_digest(instance) for values in splits.values() for instance in values]
    assert len(physical) == len(set(physical)) == 5
    assert [i.seed for i in splits['validation']] == [411, 413]
    assert [i.seed for i in splits['test']] == [421, 423]
    for usage in ['m8', 'm10']:
        first = experiment.fixed_observations(splits['validation'][0], usage)
        assert first and first == experiment.fixed_observations(splits['validation'][0], usage)
        for item in first:
            assert 'unavailability' not in item['observation']
            assert 'robots' not in item['observation']
            assert set(item['allowed_actions']) <= {None, *[t['id'] for t in item['observation']['tasks']]}


def test_benchmark_preserves_failures_freezes_selection_and_only_tests_winner(tmp_path, server):
    output = tmp_path / 'new-experiment'
    report = experiment.benchmark(output=output, models=['good:small', 'bad:large', 'missing'], repeats=1, cold=True)
    saved = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert saved == report and report['status'] == 'complete'
    assert report['inventory']['version'] == 'mock-1'
    assert report['protocol']['config']['load_timeout'] == 120
    assert report['protocol']['config']['timeout'] == 30
    assert report['protocol']['config']['scenario_budget'] == 120
    assert report['protocol']['config']['max_tokens'] == 64
    assert report['protocol']['config']['num_ctx'] == 2048
    assert report['protocol']['config']['call_budget'] == 8
    assert report['protocol']['config']['think'] is False
    assert report['protocol_sha256'] == experiment.digest(report['protocol'])
    assert report['protocol']['fixed_corpus_sha256'] == experiment.digest(report['fixed_observations'])
    assert report['exclusions'] == [dict(model='missing', reason='not_installed',
                                       install_command='ollama pull missing', download_performed=False)]
    for usage in ['m8', 'm10']:
        assert report['selection'][usage]['model'] == 'good:small'
        assert report['selection'][usage]['model_digest'] == 'digest-good:small'
        assert report['selection'][usage]['validation_only']
        bad = next(row for row in report['validation_summary'][usage] if row['model'] == 'bad:large')
        assert not bad['eligible'] and bad['reasons']
    selected = {r['model'] for r in report['runs'] if r['split'] == 'test' and r['model']}
    assert selected == {'good:small'}
    assert any(r['result']['llm_metrics']['errors'].get('invalid_json')
               for r in report['runs'] if r.get('model') == 'bad:large' and r['usage'] == 'm8')
    assert any(r['result']['llm']['errors'].get('budget_exhausted')
               for r in report['runs'] if r.get('model') == 'bad:large' and r['usage'] == 'm10')
    for index in range(2):
        for usage in ['m8', 'm10']:
            replays = [r['result'] for r in report['replay_runs'] if r['split'] == 'validation'
                       and r['index'] == index and r['usage'] == usage]
            assert len(replays) == 2
            assert replays[0]['observations_sha256'] == replays[1]['observations_sha256']
            assert [t['observation'] for t in replays[0]['llm_trace']] == [t['observation'] for t in replays[1]['llm_trace']]
    first_model_requests = [payload for url, _, payload in server if payload and payload.get('model') == 'good:small'
                            and url.rsplit('/', 1)[-1] in ['generate', 'chat']]
    assert first_model_requests[0]['keep_alive'] == 0
    assert first_model_requests[1]['prompt'] == ''
    assert 'messages' in first_model_requests[2]
    summaries = [row for row in report['comparisons'] if row['model'] == 'good:small'
                 and row['split'] == 'test' and row['usage'] == 'm10']
    assert len(summaries) == 1 and summaries[0]['runs'] == summaries[0]['complete_runs'] == 2
    assert summaries[0]['fallbacks'] == 0 and summaries[0]['generated_tokens_reported'] > 0


def _selection_fixture():
    configs = {name: OllamaConfig(model=name) for name in ['a', 'b']}
    report = dict(protocol={'repeats': 1}, splits={'validation': [{}]},
                  inventory={'models': [dict(name='a', digest='a', size_bytes=100),
                                        dict(name='b', digest='b', size_bytes=1)]}, runs=[], replay_runs=[])
    for model in configs:
        for usage in ['m8', 'm10']:
            trace = [dict(observation={'robot': {'id': 'r'}, 'tick': 0}, action='task', fallback=False,
                          structured_valid=True, action_legal=True, latency_seconds=1)]
            value = dict(llm_trace=trace, final_quality=dict(feasible=True, cardinality=1, estimated_cost=1),
                         raw_quality={'conflict_pairs': 0}, transmissions=1, assignments=[['r', 'task']],
                         rejected_actions=0, invalid_decisions=0, completion_rate=1, **{'return': -1},
                         total_tardiness=0, unfinished_tardiness_lower_bound=0, makespan=2, conflicts=0,
                         distance=1, decisions=[dict(tick=0, accepted=[['r', 'task']])])
            report['runs'].append(dict(split='validation', usage=usage, method='llm', model=model, result=value))
            report['replay_runs'].append(dict(split='validation', usage=usage, method='llm_replay', model=model,
                                              result={'llm_trace': deepcopy(trace)}))
    return report, configs


def test_selection_hard_validity_cannot_be_compensated_and_never_uses_test():
    report, configs = _selection_fixture()
    # Perfectly equal quality/latency: smaller disk footprint is the declared last tie-break.
    _, selected = experiment.select_models(report, configs)
    assert selected['m8']['model'] == selected['m10']['model'] == 'b'
    for row in report['runs']:
        if row['model'] == 'b':
            row['result']['llm_trace'][0]['fallback'] = True
            row['result']['llm_trace'][0]['action_legal'] = False
            row['result']['final_quality']['cardinality'] = 999
            row['result']['return'] = 999
    report['runs'].append(dict(split='test', model='a', usage='m10', error='poisoned test result'))
    summaries, selected = experiment.select_models(report, configs)
    assert selected['m8']['model'] == selected['m10']['model'] == 'a'
    assert not next(row for row in summaries['m8'] if row['model'] == 'b')['eligible']
    # Legal idle-only generation never proves that a generated decision was applied.
    for row in report['runs']:
        if 'result' in row:
            row['result']['llm_trace'][0]['action'] = None
    _, selected = experiment.select_models(report, configs)
    assert all(value['model'] is value['config'] is None for value in selected.values())


def test_latency_tie_break_uses_identical_replays_and_incomplete_makespan_stays_null():
    report, configs = _selection_fixture()
    for row in report['runs']:
        row['result']['llm_trace'][0]['latency_seconds'] = 100 if row['model'] == 'a' else 0.001
        if row['usage'] == 'm10':
            row['result']['completion_rate'] = 0.5
            row['result']['makespan'] = None
    for row in report['replay_runs']:
        row['result']['llm_trace'][0]['latency_seconds'] = 1 if row['model'] == 'a' else 2
    summaries, selected = experiment.select_models(report, configs)
    assert all(value['model'] == 'a' for value in selected.values())
    assert all('makespan' not in ' '.join(row['quality_order']) for row in summaries['m10'])
    aggregate = experiment.summarize_records(report['runs'])
    assert all(row['quality']['makespan']['count'] == 0 and row['quality']['makespan']['mean'] is None
               for row in aggregate if row['usage'] == 'm10')


def test_failed_preload_never_runs_probe_or_labels_calls_warm(tmp_path, server, monkeypatch):
    monkeypatch.setattr(ollama, 'preload_model', lambda config: dict(status='error', error='timeout'))
    def unexpected(config):
        raise AssertionError('warm generation must not follow failed preload')
    monkeypatch.setattr(ollama, 'probe_model', unexpected)
    report = experiment.benchmark(output=tmp_path / 'failed-preload', models=['good:small'], repeats=1)
    assert report['calibration'][0]['measurement_condition'] == 'preparation_failed'
    assert all(value['model'] is None for value in report['selection'].values())
    failed = [row for row in report['runs'] if row['model'] == 'good:small']
    assert len(failed) == 4 and all(row['error']['error'] == 'preparation_failed' for row in failed)
    assert not any(url.endswith('/chat') for url, _, _ in server)


def test_partial_export_survives_interruption_and_existing_output_is_preserved(tmp_path, server, monkeypatch):
    output = tmp_path / 'partial'
    def interrupted(config):
        partial = json.loads((output / 'report.json').read_text(encoding='utf-8'))
        assert partial['status'] == 'running' and partial['calibration']
        assert partial['runs']  # Classical references were already preserved.
        raise KeyboardInterrupt()
    monkeypatch.setattr(ollama, 'preload_model', interrupted)
    with pytest.raises(KeyboardInterrupt):
        experiment.benchmark(output=output, models=['good:small'], repeats=1)
    saved = (output / 'report.json').read_bytes()
    assert json.loads(saved)['status'] == 'interrupted'
    previous_requests = len(server)
    with pytest.raises(FileExistsError):
        experiment.benchmark(output=output, models=['good:small'], repeats=1)
    assert len(server) == previous_requests
    assert (output / 'report.json').read_bytes() == saved


def test_no_installed_candidates_produces_explicit_empty_selection(tmp_path, server):
    report = experiment.benchmark(output=tmp_path / 'missing', models=['absent'], repeats=1)
    assert all(value['model'] is None for value in report['selection'].values())
    assert not report['calibration'] and not report['replay_runs']
    assert not any(url.endswith('/chat') or url.endswith('/generate') for url, _, _ in server)


def test_cli_parameters_are_forwarded_without_network(tmp_path, monkeypatch, capsys):
    captured = {}
    def mock_benchmark(**kwargs):
        captured.update(kwargs)
        return dict(status='complete', selection={})
    monkeypatch.setattr(experiment, 'benchmark', mock_benchmark)
    assert experiment.main(['--models', 'a', 'b', '--output', str(tmp_path / 'cli'),
                            '--timeout', '31', '--load-timeout', '121', '--scenario-budget', '122',
                            '--call-budget', '9', '--max-tokens', '65', '--num-ctx', '2049',
                            '--seed', '12', '--repeats', '3', '--cold']) == 0
    assert captured['models'] == ['a', 'b'] and captured['cold']
    assert captured['timeout'] == 31 and captured['load_timeout'] == 121
    assert captured['scenario_budget'] == 122 and captured['call_budget'] == 9
    assert captured['max_tokens'] == 65 and captured['num_ctx'] == 2049
    assert captured['seed'] == 12 and captured['repeats'] == 3
    capsys.readouterr()
