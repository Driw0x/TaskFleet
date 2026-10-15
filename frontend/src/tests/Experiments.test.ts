import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import Experiments from '../components/Experiments.vue'
import MapfReplay from '../components/MapfReplay.vue'
import ScheduleDetails from '../components/ScheduleDetails.vue'
import MarlDetails from '../components/MarlDetails.vue'
import App from '../App.vue'
import type { ExperimentCatalog, ExperimentJob, Milestone, ScientificResult } from '../experiments'
import type { Instance, Result, Snapshot } from '../types'

const instance: Instance = {
  width: 3, height: 2, horizon: 3, seed: 0, obstacles: [], resources: [], unavailability: [],
  robots: [{ id: 'r1', position: [0, 0], capacity: 1 }],
  tasks: [{ id: 't1', pickup: [0, 0], destination: [1, 0], load: 1, handling_ticks: 1, deadline: 3, arrival_tick: 0 }],
}
const snapshot = (tick: number): Snapshot => ({ tick, finished: tick === 3, all_tasks_completed: tick === 3,
  robots: { r1: { position: [Math.min(tick, 1), 0], capacity: 1, state: 'idle', available: true, unavailable: false,
    task_id: null, handling_remaining: 0, resource_id: null, path: [] } },
  tasks: { t1: { state: tick === 3 ? 'completed' : 'pending', robot_id: tick ? 'r1' : null, arrival_tick: 0, completed_tick: tick === 3 ? 3 : null, late: false } }, resources: {} })
const trace: Result['trace'] = { instance, snapshots: [0, 1, 2, 3].map(snapshot), current: snapshot(3), strategy: 'fifo',
  events: [{ tick: 0, kind: 'assigned', robot_id: 'r1', task_id: 't1' }, { tick: 1, kind: 'collected', robot_id: 'r1', task_id: 't1' }, { tick: 3, kind: 'delivered', robot_id: 'r1', task_id: 't1' }] }
const problem = { width: 3, height: 2, horizon: 3, obstacles: [], agents: [{ id: 'a', start: [0, 0] as [number, number], goal: [1, 0] as [number, number] }] }
const catalog: ExperimentCatalog = {
  warehouse_instances: [{ id: 'm7.json', label: 'm7.json', instance, milestones: ['m7', 'm8', 'm10'] }],
  mapf_instances: [{ id: 'crossing.json', label: 'crossing.json', instance: problem }],
  scenario_sets: [{ id: 'scenarios.json', label: 'Scénarios', scenarios: [[], []] }],
  checkpoints: [{ id: 'q.json', label: 'q.json', source: 'historical' }],
  training_sets: [{ id: 'train-0.json', label: 'Entraînement 0' }],
  validation_sets: [{ id: 'validation-0.json', label: 'Validation 0' }],
  selected_models: { m8: 'qwen2.5:7b-instruct-q4_K_M', m10: 'qwen3:4b-instruct-2507-q4_K_M' }, limits: {},
}
function resultFor(milestone: Milestone, method: string): ScientificResult {
  const base: ScientificResult = { milestone, method, execution_status: 'valid', metrics: { tasks_completed: 1, makespan: 3, total_tardiness: 0 }, trace }
  if (milestone === 'm7') {
    const plan = { status: 'OPTIMAL', start_tick: 0, tasks: { t1: { collected_tick: 1, completed_tick: 3 } }, assignments: [{ tick: 0, robot_id: 'r1', task_id: 't1' }] }
    if (method === 'rolling') base.rolling = { replans: 2, fallback_dispatches: 1, plans: [plan] }
    else if (method === 'robust') {
      base.robust = { status: 'OPTIMAL', policy: { r1: ['t1'] }, objective: [0, 3], policies_evaluated: 2 }
      base.scenario_runs = [0, 1].map(() => ({ metrics: base.metrics, trace, execution_status: 'valid' }))
    } else Object.assign(base, { plan, execution: { executable: true } })
  } else if (milestone === 'm8') base.coordination = {
    status: 'CONFIRMED', stop_reason: 'LOCAL_OPTIMUM', domains: { r1: [null, 't1'] }, assignments: [['r1', 't1']],
    history: [{ round: 1, values: { r1: 't1' } }], raw_quality: { conflict_pairs: 0 }, confirmation_abandonments: 0,
    final_quality: { cardinality: 1, estimated_cost: 3 }, logical_messages: 1, transmissions: 1, rounds: 1,
    messages: [{ round: 1, type: 'confirmed', sender: 'r1', recipient: 'all', action: 't1' }],
  }
  else if (milestone === 'm9') {
    delete base.trace
    base.problem = problem
    base.plan = { paths: { a: [[0, 0], [0, 0], [1, 0]] }, status: 'SOLVED', optimal: method === 'cbs',
      validation: { valid: true, violations: [], metrics: { sum_of_costs: 2, makespan: 2, distance: 1, waits: 1 } } }
    base.metrics = { sum_of_costs: 2, makespan: 2, distance: 1, waits: 1 }
  } else {
    base.episode = { policy: method, terminated: true, truncated: false, return: -2, conflicts: 0, rejected_actions: 0,
      rewards: [-1, -1, 0], decisions: [{ tick: 0, observations: { r1: { legal_actions: [null, 't1'] } }, proposals: { r1: 't1' }, accepted: [['r1', 't1']], rejected: [], conflicts: [] }] }
    if (method === 'train') Object.assign(base, { checkpoint: 'session:new', training: { selected_episode: 1, validation_return: -2, training_seconds: .1, curve: [], validation: [] } })
  }
  return base
}
let wrappers: VueWrapper[]
let fetchMock: ReturnType<typeof vi.fn>
let submitted: Record<string, unknown>[]
let result: ScientificResult
let jobState: ExperimentJob['status']
let jobError: ExperimentJob['error']
let responseError: string
let installed: boolean
function job(): ExperimentJob { return { id: 'job-1', milestone: result.milestone, method: result.method, status: jobState,
  phase: jobState === 'running' ? 'loading_model' : jobState, message: jobState === 'running' ? 'Chargement Ollama en cours.' : 'Calcul terminé.',
  result: jobState === 'completed' ? result : null, error: jobError } }
beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.useFakeTimers(); wrappers = []; submitted = []; result = resultFor('m7', 'nominal'); jobState = 'completed'; jobError = null; responseError = ''; installed = true
  fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === '/api/v2/catalog') {
      const userInstance = options?.body ? JSON.parse(String(options.body)).instance : null
      return { ok: true, json: async () => userInstance
        ? { ...catalog, warehouse_instances: [{ id: 'user:current', label: 'Instance utilisateur', instance: userInstance }, ...catalog.warehouse_instances] } : catalog }
    }
    if (path === '/api/v2/ollama') return { ok: true, json: async () => ({ available: true, models: installed ? [{ name: catalog.selected_models.m8 }] : [], selected_models: catalog.selected_models, errors: [] }) }
    if (path === '/api/v2/jobs') {
      const body = JSON.parse(String(options?.body)); submitted.push(body)
      if (responseError) return { ok: false, status: 422, json: async () => ({ detail: responseError }) }
      result = resultFor(body.milestone, body.method)
      return { ok: true, json: async () => job() }
    }
    if (path === '/api/v2/jobs/job-1') return { ok: true, json: async () => job() }
    if (path === '/api/examples') return { ok: true, json: async () => ['m7.json'] }
    if (path.startsWith('/api/examples/')) return { ok: true, json: async () => ({ instance, snapshot: snapshot(0) }) }
    throw new Error(`Unexpected endpoint ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function open(milestone: Milestone = 'm7', method = 'nominal') {
  const wrapper = mount(Experiments); wrappers.push(wrapper); await flushPromises()
  await wrapper.get('[data-testid="milestone"]').setValue(milestone)
  await wrapper.get('[data-testid="method"]').setValue(method)
  await flushPromises()
  return wrapper
}
async function run(wrapper: VueWrapper) { await wrapper.get('form').trigger('submit'); await flushPromises() }

describe('V2 experiment controls and scientific results', () => {
  it.each([
    ['m7', 'nominal'], ['m7', 'rolling'], ['m7', 'robust'], ['m8', 'dsa'], ['m8', 'llm'],
    ['m9', 'prioritized'], ['m9', 'cbs'], ['m10', 'random'], ['m10', 'heuristic'], ['m10', 'q'], ['m10', 'llm'], ['m10', 'train'],
  ] as [Milestone, string][])('submits valid native form and exact method %s/%s', async (milestone, method) => {
    const wrapper = await open(milestone, method)
    expect((wrapper.get('form').element as HTMLFormElement).checkValidity()).toBe(true)
    await run(wrapper)
    expect(submitted).toHaveLength(1)
    expect(submitted[0]).toMatchObject({ milestone, method, instance: milestone === 'm9' ? problem : instance, horizon: 3 })
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(true)
    if (milestone === 'm7' && method !== 'robust') expect(submitted[0].solver_budget).toBe(.2)
    if (method === 'robust') expect(submitted[0]).toMatchObject({ scenario_set: 'scenarios.json', max_policies: 200 })
    if (milestone === 'm8') expect(submitted[0]).toMatchObject({ rounds: 20, probability: .7, seed: 0, message_budget: 10000 })
    if (milestone === 'm9') expect(submitted[0]).toMatchObject({ max_nodes: 10000, seconds: 2 })
    if (method === 'prioritized') expect(submitted[0].priorities).toEqual(['a'])
    if (method === 'cbs') expect(submitted[0]).not.toHaveProperty('priorities')
    if (method === 'q') expect(submitted[0].checkpoint).toBe('q.json')
    if (method === 'llm') expect(submitted[0]).toMatchObject({ model: null, preload: true, load_timeout: 120, call_budget: 8 })
    if (method === 'train') expect(submitted[0]).toMatchObject({ episodes: 10, training_instances: ['train-0.json'], validation_instances: ['validation-0.json'] })
  })

  it('transmits rolling parameters and shows planned versus actual dates and fallbacks', async () => {
    const wrapper = await open('m7', 'rolling')
    await wrapper.get('[data-testid="window"]').setValue(12)
    await wrapper.get('[data-testid="frequency"]').setValue(2)
    await run(wrapper)
    expect(submitted[0]).toMatchObject({ window: 12, frequency: 2 })
    expect(wrapper.get('[data-testid="schedule-table"]').text()).toContain('Livraison / fin')
    expect(wrapper.get('[data-testid="schedule-table"]').text()).toContain('Réalisé')
    expect(wrapper.text()).toContain('Replanifications')
    expect(wrapper.text()).toContain('Décisions de secours')
  })

  it('shows DSA domains, local history, conflicts and confirmation messages', async () => {
    const wrapper = await open('m8', 'dsa')
    await wrapper.get('[data-testid="rounds"]').setValue(7)
    await wrapper.get('[data-testid="probability"]').setValue(.5)
    await wrapper.get('[data-testid="message-budget"]').setValue(120)
    await run(wrapper)
    expect(submitted[0]).toMatchObject({ rounds: 7, probability: .5, message_budget: 120 })
    expect(wrapper.get('[data-testid="domains"]').text()).toContain('[null,"t1"]')
    expect(wrapper.get('[data-testid="coordination-status"]').text()).toContain('Conflits avant confirmation : 0')
    expect(wrapper.text()).toContain('confirmed')
    await wrapper.get('[data-testid="log-task"]').setValue('t1')
    expect(wrapper.get('[data-testid="log-count"]').text()).toContain('1 / 1')
  })

  it('replays MAPF waits and persistent goals separately from warehouse physics', async () => {
    const wrapper = await open('m9', 'cbs'); await run(wrapper)
    expect(wrapper.findComponent(MapfReplay).exists()).toBe(true)
    expect(wrapper.find('svg[aria-label="Entrepôt interactif"]').exists()).toBe(false)
    await wrapper.get('input[aria-label="Tick du rejeu"]').setValue(1)
    expect(wrapper.get('[data-position="0,0"]').attributes('data-waiting')).toBe('true')
    await wrapper.get('input[aria-label="Tick du rejeu"]').setValue(3)
    expect(wrapper.get('[data-position="1,0"]').text()).toContain('Destination atteinte')
    expect(wrapper.get('[data-testid="mapf-status"]').text()).toContain('Plan vérifié : Oui')
  })

  it('shows Q evaluation rewards, observations, proposals, confirmations and V1 replay', async () => {
    const wrapper = await open('m10', 'q'); await run(wrapper)
    expect(wrapper.get('[data-testid="rewards"]').text()).toContain('Retour cumulé')
    expect(wrapper.get('[data-testid="episode-decision"]').text()).toContain('legal_actions')
    expect(wrapper.get('[data-testid="episode-decision"]').text()).toContain('accepted')
    expect(wrapper.find('svg[aria-label="Entrepôt interactif"]').exists()).toBe(true)
    await wrapper.get('[aria-label="Dernier tick"]').trigger('click')
    expect(wrapper.get('svg').text()).toContain('tick 3')
  })

  it('keeps the trained checkpoint available for a subsequent frozen evaluation', async () => {
    const wrapper = await open('m10', 'train')
    await wrapper.get('[data-testid="episodes"]').setValue(2)
    await wrapper.get('[data-testid="checkpoint-every"]').setValue(1)
    await run(wrapper)
    expect(submitted[0]).toMatchObject({ episodes: 2, checkpoint_every: 1 })
    expect(wrapper.get('[data-testid="training-result"]').text()).toContain('session:new')
    await wrapper.get('[data-testid="method"]').setValue('q')
    await flushPromises()
    expect(wrapper.get('[data-testid="checkpoint"]').text()).toContain('Checkpoint entraîné')
    await run(wrapper)
    expect(submitted[1].checkpoint).toBe('session:new')
  })

  it.each(['m7', 'm8', 'm9', 'm10'] as Milestone[])('displays API validation errors for %s', async milestone => {
    const method = { m7: 'nominal', m8: 'dsa', m9: 'cbs', m10: 'q' }[milestone]
    const wrapper = await open(milestone, method); responseError = 'Budget ou instance invalide.'
    await run(wrapper)
    expect(wrapper.get('[role="alert"]').text()).toContain(responseError)
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
  })

  it.each([['m7', 'nominal', 'solver-budget', 0], ['m8', 'dsa', 'probability', 2], ['m9', 'cbs', 'max-nodes', 0], ['m10', 'train', 'episodes', 31]] as [Milestone, string, string, number][])
  ('prevents invalid %s input before submitting', async (milestone, method, input, value) => {
    const wrapper = await open(milestone, method)
    await wrapper.get(`[data-testid="${input}"]`).setValue(value); await run(wrapper)
    expect(submitted).toHaveLength(0)
  })

  it('keeps long Ollama loading asynchronous, then renders budget errors and fallback details', async () => {
    const wrapper = await open('m8', 'llm'); jobState = 'running'; await run(wrapper)
    await vi.advanceTimersByTimeAsync(61000)
    expect(wrapper.get('[data-testid="job-status"]').text()).toContain('Chargement Ollama en cours')
    expect(wrapper.find('[data-testid="v2-error"]').exists()).toBe(false)
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
    result.execution_status = 'budget_exhausted'
    result.coordination!.llm_metrics = { calls: 1, successes: 0, fallbacks: 1, errors: { timeout: 1, scenario_budget_exhausted: 1 } }
    result.coordination!.llm_trace = [{ action: null, fallback: true, error: 'timeout', action_legal: false }]
    jobState = 'completed'; await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="partial-result"]').text()).toContain('Budget épuisé')
    expect(wrapper.get('[data-testid="llm-result"]').text()).toContain('timeout')
    expect(wrapper.get('[data-testid="llm-result"]').text()).toContain('Secours')
  })

  it('ignores a model-loading job response after browser navigation changes the problem', async () => {
    const wrapper = await open('m8', 'llm'); jobState = 'running'; await run(wrapper)
    const original = fetchMock.getMockImplementation() as (path: string, options?: RequestInit) => unknown
    let resolvePoll!: (value: unknown) => void
    fetchMock.mockImplementation((path: string, options?: RequestInit) => path === '/api/v2/jobs/job-1'
      ? new Promise(resolve => { resolvePoll = resolve }) : original(path, options))
    await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="job-status"]').text()).toContain('Chargement Ollama')
    await wrapper.setProps({ milestone: 'm9' }); await flushPromises()
    jobState = 'completed'
    resolvePoll({ ok: true, json: async () => job() }); await flushPromises()
    expect(wrapper.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm9')
    expect(wrapper.get('[data-testid="method"]').element).toHaveProperty('value', 'prioritized')
    expect(wrapper.get('[data-testid="v2-instance"]').element).toHaveProperty('value', 'crossing.json')
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="llm-result"]').exists()).toBe(false)
  })

  it('checks installed models, reports absent models, and sends a manual identifier', async () => {
    const wrapper = await open('m8', 'llm'); installed = false
    await wrapper.get('[data-testid="check-ollama"]').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain(`Modèle absent : ${catalog.selected_models.m8}`)
    await wrapper.get('[data-testid="manual-model"]').setValue('custom:7b'); await run(wrapper)
    expect(submitted[0].model).toBe('custom:7b')
  })

  it('shows explicit Ollama job failure without a successful result', async () => {
    const wrapper = await open('m10', 'llm'); jobState = 'failed'; jobError = { kind: 'ollama_error', message: 'Model incompatible' }
    await run(wrapper)
    expect(wrapper.get('[data-testid="v2-error"]').text()).toContain('ollama_error : Model incompatible')
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
  })

  it.each([
    ['m8', 'valid', { calls: 1, successes: 1, legal_actions: 1, fallbacks: 0, errors: {} }, false],
    ['m10', 'ollama_error', { calls: 1, successes: 0, legal_actions: 0, fallbacks: 1, errors: { invalid_json: 1 } }, true],
  ] as const)('renders %s Ollama validation and generated actions', async (milestone, status, metrics, fallback) => {
    const wrapper = await open(milestone, 'llm'); jobState = 'running'; await run(wrapper)
    result.execution_status = status
    const actionTrace = [{ action: fallback ? null : 't1', fallback, error: fallback ? 'invalid_json' : null, action_legal: !fallback }]
    if (milestone === 'm8') Object.assign(result.coordination!, { llm_metrics: metrics, llm_trace: actionTrace })
    else Object.assign(result.episode!, { llm: metrics, llm_trace: actionTrace, llm_actions_accepted: 0 })
    jobState = 'completed'; await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="llm-result"]').text()).toContain('Générations valides')
    expect(wrapper.get('[data-testid="llm-result"]').text()).toContain(fallback ? 'Secours' : 'Action t1')
    if (fallback) expect(wrapper.get('[data-testid="partial-result"]').text()).toContain('Erreur Ollama')
  })

  it.each([0, 1])('shows token counts only with available measurements (%i)', async measurements => {
    const wrapper = await open('m8', 'llm'); jobState = 'running'; await run(wrapper)
    result.coordination!.llm_metrics = { calls: 1, token_measurements: measurements, prompt_tokens: 0, generated_tokens: 0 }
    jobState = 'completed'; await vi.advanceTimersByTimeAsync(500)
    const metrics = wrapper.get('[data-testid="llm-result"] .metrics').text()
    expect(metrics.includes('Tokens en entrée')).toBe(measurements > 0)
    expect(metrics.includes('Tokens générés')).toBe(measurements > 0)
  })

  it('compares robust worst-case metrics and synchronizes metrics with the replayed scenario', async () => {
    const wrapper = await open('m7', 'robust'); jobState = 'running'
    await wrapper.get('[data-testid="v2-compare"]').trigger('click'); await flushPromises()
    result.scenario_runs![1].metrics = { tasks_completed: 1, makespan: 7, total_tardiness: 4 }
    result.comparison = [{ method: 'robust', execution_status: 'valid', metrics: result.metrics, scenario_runs: result.scenario_runs },
      { method: 'nominal_fixed_queues', execution_status: 'incomplete', metrics: result.metrics,
        scenario_runs: [{ metrics: { makespan: null, total_tardiness: 2 }, execution_status: 'incomplete' }] }]
    jobState = 'completed'; await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="v2-comparison"]').text()).toContain('Pire retard (scénarios)')
    expect(wrapper.get('[data-testid="v2-comparison"]').text()).toContain('2 / 2')
    expect(wrapper.get('[data-testid="v2-comparison"]').text()).toContain('0 / 1')
    await wrapper.get('[data-testid="replay-scenario"]').setValue(1)
    const metrics = wrapper.get('[data-testid="v2-result"] .metrics').text()
    expect(metrics).toContain('Makespan (ticks)7')
    expect(metrics).toContain('Retard total4')
  })

  it('pauses local replay when leaving V2 and clears timers on unmount', async () => {
    const wrapper = await open('m9', 'cbs'); await run(wrapper)
    const calls = fetchMock.mock.calls.length
    await wrapper.get('[data-testid="v2-play"]').trigger('click'); await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('input[aria-label="Tick du rejeu"]').element).toHaveProperty('value', '1')
    await wrapper.setProps({ active: false }); await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('input[aria-label="Tick du rejeu"]').element).toHaveProperty('value', '1')
    expect(fetchMock).toHaveBeenCalledTimes(calls)
    wrapper.unmount(); expect(vi.getTimerCount()).toBe(0)
  })

  it('renders method comparison and explicit priority failure rather than infeasibility', async () => {
    const wrapper = await open('m9', 'prioritized'); jobState = 'running'; await wrapper.get('[data-testid="v2-compare"]').trigger('click'); await flushPromises()
    expect(submitted[0].compare).toBe(true)
    result.execution_status = 'priority_failed'; result.plan = { status: 'PRIORITY_FAILED', paths: null, validation: null }
    result.comparison = [{ method: 'prioritized', execution_status: 'priority_failed', metrics: {} }, { method: 'cbs', execution_status: 'valid', metrics: { sum_of_costs: 2 } }]
    jobState = 'completed'; await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="partial-result"]').text()).toContain('infaisabilité non prouvée')
    expect(wrapper.get('[data-testid="v2-comparison"]').text()).toContain('CBS')
    expect(wrapper.get('[data-testid="v2-play"]').attributes('disabled')).toBeDefined()
  })

  it('preserves V1 state while opening V2 and returning to V1', async () => {
    const wrapper = mount(App); wrappers.push(wrapper); await flushPromises()
    await wrapper.get('[data-testid="strategy"]').setValue('cnp')
    await wrapper.get('[data-testid="nav-v2"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="experiments"]').isVisible()).toBe(true)
    expect(wrapper.get('[data-testid="strategy"]').isVisible()).toBe(false)
    await wrapper.get('[data-testid="nav-v1"]').trigger('click')
    expect((wrapper.get('[data-testid="strategy"]').element as HTMLSelectElement).value).toBe('cnp')
    expect(wrapper.get('[data-testid="experiments"]').attributes('style')).toContain('display: none')
  })

  it('keeps a compatible instance across problems and methods and uses scientific problem labels', async () => {
    const wrapper = await open()
    const before = (wrapper.get('[data-testid="v2-instance"]').element as HTMLSelectElement).value
    await wrapper.get('[data-testid="milestone"]').setValue('m8'); await flushPromises()
    expect(wrapper.get('[data-testid="v2-instance"]').element).toHaveProperty('value', before)
    await wrapper.get('[data-testid="method"]').setValue('llm'); await flushPromises()
    expect(wrapper.get('[data-testid="v2-instance"]').element).toHaveProperty('value', before)
    expect(wrapper.get('[data-testid="milestone"]').text()).toBe('OrdonnancementAllocation distribuéePlanification multi-agentApprentissage coopératif')
    expect(wrapper.get('h2').text()).toBe('Expériences scientifiques')
  })

  it('applies a native problem selection on its first input/change sequence after a result', async () => {
    const wrapper = await open(); await run(wrapper)
    const problemSelect = wrapper.get('[data-testid="milestone"]')
    ;(problemSelect.element as HTMLSelectElement).value = 'm9'
    await problemSelect.trigger('input')
    await problemSelect.trigger('change'); await flushPromises()
    expect(problemSelect.element).toHaveProperty('value', 'm9')
    expect(wrapper.get('[data-testid="method"]').text()).toContain('CBS')
    expect(wrapper.get('[data-testid="v2-instance"]').text()).toContain('crossing.json')
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
  })

  it('preserves a native method or checkbox change before clearing its result', async () => {
    const wrapper = await open(); await run(wrapper)
    const methodSelect = wrapper.get('[data-testid="method"]')
    ;(methodSelect.element as HTMLSelectElement).value = 'rolling'
    await methodSelect.trigger('input'); await methodSelect.trigger('change'); await flushPromises()
    expect(methodSelect.element).toHaveProperty('value', 'rolling')
    expect(wrapper.find('[data-testid="window"]').exists()).toBe(true)
    await wrapper.get('[data-testid="milestone"]').setValue('m8')
    await wrapper.get('[data-testid="method"]').setValue('llm'); await flushPromises(); await run(wrapper)
    const checkbox = wrapper.get('[data-testid="preload"]')
    ;(checkbox.element as HTMLInputElement).checked = false
    await checkbox.trigger('input'); await checkbox.trigger('change'); await run(wrapper)
    expect(submitted.at(-1)!.preload).toBe(false)
  })

  it('ignores an older problem catalog after a single rapid switch to MAPF', async () => {
    const wrapper = await open(); await run(wrapper)
    const original = fetchMock.getMockImplementation() as (path: string, options?: RequestInit) => unknown
    let resolveOld!: (value: unknown) => void
    fetchMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/v2/catalog' && options?.body && JSON.parse(String(options.body)).milestone === 'm8') return new Promise(resolve => { resolveOld = resolve })
      return original(path, options)
    })
    await wrapper.get('[data-testid="milestone"]').setValue('m8')
    await wrapper.get('[data-testid="milestone"]').setValue('m9'); await flushPromises()
    resolveOld({ ok: true, json: async () => ({ ...catalog, mapf_instances: [] }) }); await flushPromises()
    expect(wrapper.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm9')
    expect(wrapper.get('[data-testid="method"]').element).toHaveProperty('value', 'prioritized')
    expect(wrapper.get('[data-testid="v2-instance"]').element).toHaveProperty('value', 'crossing.json')
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
  })

  it('keeps replay controls before the calendar and updates both calendar and spatial state', async () => {
    const wrapper = await open(); await run(wrapper)
    const controls = wrapper.get('[data-testid="experiment-playback"]').element
    const calendar = wrapper.get('[data-testid="schedule-details"]').element
    expect(controls.compareDocumentPosition(calendar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const calls = fetchMock.mock.calls.length
    await wrapper.get('[aria-label="Tick suivant"]').trigger('click')
    expect(wrapper.get('[data-testid="schedule-current-tick"]').text()).toContain('tick 1')
    expect(wrapper.get('[data-stage="collection"]').text()).toContain('Réalisé : tick 1')
    expect(wrapper.get('svg[aria-label="Entrepôt interactif"] title').text()).toContain('tick 1')
    expect(fetchMock).toHaveBeenCalledTimes(calls)
  })

  it.each([['m7', 'nominal', ScheduleDetails], ['m10', 'q', MarlDetails]] as const)
  ('synchronizes %s chart selection with the spatial replay without API calls', async (milestone, method, component) => {
    const wrapper = await open(milestone, method); await run(wrapper)
    const calls = fetchMock.mock.calls.length
    wrapper.findComponent(component).vm.$emit('seek', 2); await wrapper.vm.$nextTick()
    expect(wrapper.get('input[aria-label="Tick du rejeu"]').element).toHaveProperty('value', '2')
    expect(wrapper.get('svg[aria-label="Entrepôt interactif"] title').text()).toContain('tick 2')
    expect(fetchMock).toHaveBeenCalledTimes(calls)
  })

  it('removes charts immediately on rapid instance and problem changes and ignores old results', async () => {
    const wrapper = await open('m10', 'q'); await run(wrapper)
    expect(wrapper.find('[data-testid="marl-details"]').exists()).toBe(true)
    await wrapper.setProps({ userInstance: instance }); await flushPromises()
    await wrapper.get('[data-testid="v2-instance"]').setValue('user:current')
    expect(wrapper.find('[data-testid="marl-details"]').exists()).toBe(false)
    jobState = 'running'; await run(wrapper)
    const original = fetchMock.getMockImplementation() as (path: string, options?: RequestInit) => unknown
    let resolveOld!: (value: unknown) => void
    fetchMock.mockImplementation((path: string, options?: RequestInit) => path === '/api/v2/jobs/job-1'
      ? new Promise(resolve => { resolveOld = resolve }) : original(path, options))
    await vi.advanceTimersByTimeAsync(500)
    await wrapper.get('[data-testid="milestone"]').setValue('m8'); await flushPromises()
    jobState = 'completed'; resolveOld({ ok: true, json: async () => job() }); await flushPromises()
    expect(wrapper.find('[data-testid="marl-details"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="coordination-details"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="comparison-charts"]').exists()).toBe(false)
  })

  it('renders backend compatibility reasons and disables launch for an empty list', async () => {
    const wrapper = await open()
    const original = fetchMock.getMockImplementation() as (path: string, options?: RequestInit) => unknown
    fetchMock.mockImplementation((path: string, options?: RequestInit) => path === '/api/v2/catalog' && options?.body
      ? { ok: true, json: async () => ({ ...catalog, warehouse_instances: [], mapf_instances: [], incompatible_instances: [{ id: 'm7.json', label: 'm7.json', format: 'warehouse', reason: 'Unknown robust scenario set' }] }) }
      : original(path, options))
    await wrapper.get('[data-testid="method"]').setValue('robust'); await flushPromises()
    expect(wrapper.get('[data-testid="no-compatible-instance"]').text()).toContain('Aucune instance compatible')
    expect(wrapper.get('[data-testid="v2-run"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Unknown robust scenario set')
    await run(wrapper); expect(submitted).toHaveLength(0)
  })

  it('ignores an obsolete compatibility response after a rapid method change', async () => {
    const wrapper = await open()
    const original = fetchMock.getMockImplementation() as (path: string, options?: RequestInit) => unknown
    let resolveOld!: (value: unknown) => void
    fetchMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/v2/catalog' && options?.body && JSON.parse(String(options.body)).method === 'rolling') return new Promise(resolve => { resolveOld = resolve })
      return original(path, options)
    })
    await wrapper.get('[data-testid="method"]').setValue('rolling')
    expect(wrapper.get('[data-testid="v2-run"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="method"]').setValue('nominal'); await flushPromises()
    resolveOld({ ok: true, json: async () => ({ ...catalog, warehouse_instances: [] }) }); await flushPromises()
    expect(wrapper.get('[data-testid="v2-instance"]').text()).toContain('m7.json')
    expect(wrapper.get('[data-testid="v2-run"]').attributes('disabled')).toBeUndefined()
  })

  it('invalidates results as soon as a parameter is typed and clears selections', async () => {
    const wrapper = await open(); await run(wrapper)
    await wrapper.get('[data-testid="inspect-task"]').setValue('t1')
    expect(wrapper.find('[data-testid="task-inspection"]').exists()).toBe(true)
    await wrapper.get('[data-testid="v2-horizon"]').setValue(2)
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
    await run(wrapper)
    expect(wrapper.find('[data-testid="task-inspection"]').exists()).toBe(false)
  })

  it('sends the current imported instance for backend validation and ignores its obsolete pending job', async () => {
    const wrapper = await open()
    await wrapper.setProps({ userInstance: instance }); await flushPromises()
    await wrapper.get('[data-testid="v2-instance"]').setValue('user:current')
    jobState = 'running'; await run(wrapper)
    await wrapper.setProps({ userInstance: { ...instance, seed: 9 } }); await flushPromises()
    const catalogCalls = fetchMock.mock.calls.filter(([path, options]) => path === '/api/v2/catalog' && options?.body)
    expect(JSON.parse(String(catalogCalls.at(-1)![1]!.body)).instance.seed).toBe(9)
    jobState = 'completed'; await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
  })

  it('preserves a catalog result when an unrelated simulation instance finishes loading', async () => {
    const wrapper = await open(); await run(wrapper)
    await wrapper.setProps({ userInstance: instance }); await flushPromises()
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="v2-instance"]').element).toHaveProperty('value', 'm7.json')
    await wrapper.get('[data-testid="v2-instance"]').setValue('user:current')
    expect(wrapper.find('[data-testid="v2-result"]').exists()).toBe(false)
  })
})
