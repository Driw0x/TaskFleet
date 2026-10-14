import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import App from '../App.vue'
import EventLog from '../components/EventLog.vue'
import InstanceEditor from '../components/InstanceEditor.vue'
import Warehouse from '../components/Warehouse.vue'
// Captured from the unchanged Python engine (contention FIFO and dynamic CNP).
import inspectionData from './fixtures/inspection.json'
import { download, request } from '../api'
import type { Comparison, Instance, Preview, Result, Snapshot, TraceEvent } from '../types'
const inspection = inspectionData as unknown as Record<'contention' | 'dynamic', { instance: Instance; snapshots: Snapshot[]; events: TraceEvent[] }>

const instance: Instance = {
  width: 3, height: 2, horizon: 3, seed: null, obstacles: [[1, 1]],
  robots: [{ id: 'r1', position: [0, 0], capacity: 2 }, { id: 'r2', position: [2, 1], capacity: 1 }],
  tasks: [{ id: 't1', pickup: [1, 0], destination: [2, 0], load: 1, handling_ticks: 1, deadline: null, arrival_tick: 0 }],
  resources: [{ id: 'station', position: [1, 0], capacity: 1 }], unavailability: [],
}
function snapshot(tick: number): Snapshot {
  return { tick, finished: tick === 3, all_tasks_completed: false,
    robots: { r1: { position: [Math.min(tick, 2), 0], capacity: 2, state: tick ? 'to_delivery' : 'idle', available: tick === 0, unavailable: false, task_id: tick ? 't1' : null, handling_remaining: tick === 1 ? 1 : 0, resource_id: tick === 1 ? 'station' : null, path: tick === 1 ? [[2, 0]] : [] }, r2: { position: [2, 1], capacity: 1, state: 'idle', available: true, unavailable: false, task_id: null, handling_remaining: 0, resource_id: null, path: [] } },
    tasks: { t1: { state: tick ? 'assigned' : 'pending', robot_id: tick ? 'r1' : null, arrival_tick: 0, completed_tick: null, late: false } },
    resources: { station: { position: [1, 0], capacity: 1, holders: tick === 1 ? ['r1'] : [] } } }
}
const preview: Preview = { instance, snapshot: snapshot(0) }
const events: TraceEvent[] = [
  { tick: 0, kind: 'message', type: 'offer', sender: 'r1', recipient: 'r2', task_id: 't1', cost: 4 },
  { tick: 0, kind: 'message', type: 'refuse', sender: 'r2', recipient: 'r1', task_id: 't1', reason: 'capacity' },
  { tick: 1, kind: 'moved', robot_id: 'r1', task_id: 't1', position: [1, 0] },
  { tick: 2, kind: 'message', type: 'award', sender: 'r2', recipient: 'r1', task_id: 't1', cost: 4 },
]
const result: Result = { trace: { instance, snapshots: [0, 1, 2, 3].map(snapshot), current: snapshot(3), strategy: 'cnp', events }, metrics: { tasks_completed: 0, makespan: null, messages: 3, distance: 2 } }
const report: Comparison = { runs: ['fifo', 'cpsat', 'cnp'].map(strategy => ({ strategy: strategy as 'fifo', status: 'incomplete', error: null, tasks_completed: 0, makespan: null, distance: 2, completion_rate: 0, messages: strategy === 'cnp' ? 3 : 0 })) }
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
let wrappers: VueWrapper[]
let fetchMock: ReturnType<typeof vi.fn>
let respond: (path: string, body?: Record<string, unknown>) => unknown

beforeEach(() => {
  wrappers = []
  respond = (path, body) => {
    if (path === '/api/examples') return ['complex_comparison.json', 'simple.json']
    if (path.startsWith('/api/examples/')) return clone(preview)
    if (path === '/api/simulations') return clone(result)
    if (path === '/api/comparisons') return clone(report)
    if (path === '/api/instances/generate') return { ...clone(preview), instance: { ...clone(instance), width: body?.width, height: body?.height, seed: body?.seed } }
    if (path === '/api/instances/validate') {
      const data = typeof body?.instance === 'string' ? JSON.parse(body.instance) : body?.instance
      if ((data as Instance).width < 3) throw new Error('robots[1]: outside grid')
      return { instance: data, snapshot: snapshot(0) }
    }
    throw new Error(`Unexpected endpoint ${path}`)
  }
  fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
    try { return { ok: true, json: async () => await respond(path, options?.body ? JSON.parse(String(options.body)) : undefined) } }
    catch (caught) { return { ok: false, json: async () => ({ detail: (caught as Error).message }) } }
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function app() {
  const wrapper = mount(App)
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
async function run(wrapper: VueWrapper) { await wrapper.get('[data-testid="run"]').trigger('click'); await flushPromises() }

describe('TaskFleet UI', () => {
  it('preserves HTTP status when a proxy returns a non-JSON error', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 502, json: async () => { throw new SyntaxError('Unexpected token') } })
    await expect(request('examples')).rejects.toThrow('Erreur HTTP 502')
  })
  it('loads Python preview and displays SVG entities', async () => {
    const wrapper = await app()
    expect(wrapper.get('svg').text()).toContain('Entrepôt 3 × 2')
    expect(wrapper.findAll('[data-robot]')).toHaveLength(2)
    expect(wrapper.get('[data-robot="r1"]').attributes('data-position')).toBe('0,0')
    expect(wrapper.text()).toContain('Station')
    expect(wrapper.get('[data-testid="run"]').attributes('disabled')).toBeUndefined()
  })

  it('replays previous/next/slider/end/restart without network and synchronizes inspection/events', async () => {
    const wrapper = await app()
    await run(wrapper)
    const calls = fetchMock.mock.calls.length
    await wrapper.get('[data-robot="r1"]').trigger('click')
    await wrapper.get('[aria-label="Tick suivant"]').trigger('click')
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('1')
    expect(wrapper.get('[data-testid="inspection"]').text()).toContain('[1,0]')
    expect(wrapper.get('[data-testid="inspection"]').text()).toContain('station')
    expect(wrapper.text()).toContain('moved')
    await wrapper.get('[aria-label="Tick précédent"]').trigger('click')
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('0')
    await wrapper.get('[aria-label="Tick"]').setValue('2')
    expect(wrapper.get('[data-robot="r1"]').attributes('data-position')).toBe('2,0')
    await wrapper.get('[aria-label="Dernier tick"]').trigger('click')
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('3')
    await wrapper.get('[aria-label="Recommencer"]').trigger('click')
    await wrapper.get('[aria-label="Désélectionner"]').trigger('click')
    expect(wrapper.text()).toContain('Vue globale')
    expect(fetchMock).toHaveBeenCalledTimes(calls)
  })

  it('plays, pauses, changes speed and stops at last snapshot locally', async () => {
    const wrapper = await app()
    await run(wrapper)
    vi.useFakeTimers()
    const calls = fetchMock.mock.calls.length
    await wrapper.get('[data-testid="play"]').trigger('click')
    await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('1')
    await wrapper.get('[data-testid="play"]').trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('1')
    await wrapper.get('[data-testid="speed"]').setValue('4')
    await wrapper.get('[data-testid="play"]').trigger('click')
    await vi.advanceTimersByTimeAsync(125)
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('2')
    await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('3')
    expect(wrapper.get('[data-testid="play"]').text()).toBe('Lecture')
    expect(fetchMock).toHaveBeenCalledTimes(calls)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('restarts timer when speed changes during playback and cleans up on unmount', async () => {
    const wrapper = await app()
    await run(wrapper)
    vi.useFakeTimers()
    await wrapper.get('[data-testid="play"]').trigger('click')
    await wrapper.get('[data-testid="speed"]').setValue('2')
    await vi.advanceTimersByTimeAsync(250)
    expect(wrapper.get('[data-testid="tick"]').text()).toBe('1')
    wrapper.unmount()
    wrappers = []
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps comparison through replay and strategy changes, invalidates on edits and budget', async () => {
    const wrapper = await app()
    await wrapper.get('[data-testid="compare"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="comparison"]').text()).toContain('Incomplet')
    await run(wrapper)
    await wrapper.get('[aria-label="Tick suivant"]').trigger('click')
    await wrapper.get('[data-testid="strategy"]').setValue('cnp')
    expect(wrapper.find('[data-testid="comparison"]').exists()).toBe(true)
    await wrapper.get('[data-testid="budget"]').setValue('0.5')
    expect(wrapper.find('[data-testid="comparison"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('comparaison invalidée')
    vi.useFakeTimers()
    await wrapper.get('[data-testid="edit-horizon"]').setValue('4')
    await vi.advanceTimersByTimeAsync(200); await flushPromises()
    await wrapper.get('[data-testid="compare"]').trigger('click'); await flushPromises()
    await wrapper.get('[data-testid="edit-width"]').setValue('2')
    expect(wrapper.find('[data-testid="comparison"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(200); await flushPromises()
    expect(wrapper.text()).toContain('outside grid')
    expect(wrapper.get('[data-testid="run"]').attributes('disabled')).toBeDefined()
    expect((wrapper.get('[data-testid="edit-width"]').element as HTMLInputElement).value).toBe('2')
  })

  it('generates on submit, sends preserved environment and installs editable preview', async () => {
    const wrapper = await app()
    await wrapper.get('[data-testid="generate-width"]').setValue('8')
    await wrapper.get('[data-testid="generate-height"]').setValue('6')
    await wrapper.get('[data-testid="generate-robot_count"]').setValue('3')
    await wrapper.get('[data-testid="generate-task_count"]').setValue('10')
    await wrapper.get('[data-testid="generate-seed"]').setValue('42')
    expect(fetchMock.mock.calls.filter(([path]) => path.endsWith('generate'))).toHaveLength(0)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    const body = JSON.parse(fetchMock.mock.calls.find(([path]) => path.endsWith('generate'))![1].body)
    expect(body).toEqual({ width: 8, height: 6, robot_count: 3, task_count: 10, seed: 42, horizon: 3, obstacles: instance.obstacles, resources: instance.resources })
    expect(wrapper.get('svg').text()).toContain('8 × 6')
    expect((wrapper.get('[data-testid="edit-seed"]').element as HTMLInputElement).value).toBe('42')
  })

  it('imports raw JSON via Python, preserves invalid input for correction', async () => {
    const wrapper = await app()
    const modified = { ...instance, width: 5 }
    wrapper.findComponent(InstanceEditor).vm.$emit('import', JSON.stringify(modified))
    await flushPromises()
    expect(wrapper.get('svg').text()).toContain('5 × 2')
    wrapper.findComponent(InstanceEditor).vm.$emit('import', '{"width":')
    await flushPromises()
    expect(wrapper.get('[data-testid="run"]').attributes('disabled')).toBeDefined()
    expect((wrapper.get('[data-testid="json-draft"]').element as HTMLTextAreaElement).value).toBe('{"width":')
    await wrapper.get('[data-testid="json-draft"]').setValue(JSON.stringify(instance))
    await wrapper.get('[data-testid="apply-json"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="run"]').attributes('disabled')).toBeUndefined()
    expect(fetchMock.mock.calls.some(([path, options]) => path.endsWith('validate') && typeof JSON.parse(options.body).instance === 'string')).toBe(true)
  })

  it('renders API errors and loading state, clears failed comparison', async () => {
    const wrapper = await app()
    respond = () => { throw new Error('Calcul impossible.') }
    await wrapper.get('[data-testid="compare"]').trigger('click')
    expect(wrapper.get('[data-testid="compare"]').text()).toBe('Comparaison…')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Calcul impossible.')
    expect(wrapper.find('[data-testid="comparison"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="compare"]').attributes('disabled')).toBeUndefined()
  })

  it('ignores a simulation response after instance replacement', async () => {
    const wrapper = await app()
    const standard = respond
    let finish!: (value: Result) => void
    respond = (path, body) => path.endsWith('simulations') ? new Promise(resolve => { finish = resolve }) : standard(path, body)
    await wrapper.get('[data-testid="run"]').trigger('click')
    await wrapper.get('[data-testid="load-example"]').trigger('click'); await flushPromises()
    finish(clone(result)); await flushPromises()
    expect(wrapper.get('[data-testid="play"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.secondary').exists()).toBe(false)
  })

  it('ignores an older example response after editing', async () => {
    const wrapper = await app()
    const standard = respond
    let finish!: (value: Preview) => void
    let defer = true
    respond = (path, body) => path.startsWith('/api/examples/') && defer ? new Promise(resolve => { finish = resolve }) : standard(path, body)
    await wrapper.get('[data-testid="load-example"]').trigger('click')
    defer = false
    await wrapper.get('[data-testid="edit-width"]').setValue('5')
    finish(clone(preview)); await flushPromises()
    expect((wrapper.get('[data-testid="edit-width"]').element as HTMLInputElement).value).toBe('5')
  })

  it('ignores obsolete validation responses and comparison reports', async () => {
    const wrapper = await app()
    const standard = respond
    let finishValidation!: (value: Preview) => void
    let finishComparison!: (value: Comparison) => void
    respond = (path, body) => path.endsWith('validate') ? new Promise(resolve => { finishValidation = resolve })
      : path.endsWith('comparisons') ? new Promise(resolve => { finishComparison = resolve }) : standard(path, body)
    await wrapper.get('[data-testid="compare"]').trigger('click')
    vi.useFakeTimers()
    await wrapper.get('[data-testid="edit-width"]').setValue('5')
    await vi.advanceTimersByTimeAsync(200)
    await wrapper.get('[data-testid="edit-width"]').setValue('2')
    finishValidation({ ...preview, instance: { ...instance, width: 5 } })
    finishComparison(clone(report))
    await flushPromises()
    expect(wrapper.get('[data-testid="run"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="comparison"]').exists()).toBe(false)
    expect(wrapper.get('svg').text()).toContain('3 × 2')
  })

  it('keeps the editable instance after generation fails', async () => {
    const wrapper = await app()
    const standard = respond
    respond = (path, body) => {
      if (path.endsWith('generate')) throw new Error('Au moins un robot est nécessaire')
      return standard(path, body)
    }
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Au moins un robot')
    expect(wrapper.get('svg').text()).toContain('3 × 2')
    expect(wrapper.get('[data-testid="run"]').attributes('disabled')).toBeUndefined()
  })

  it('handles zero horizon and partial comparison failures without fabricated metrics', async () => {
    const original = respond
    respond = (path, body) => path.endsWith('simulations') ? { ...result, trace: { ...result.trace, current: snapshot(0), snapshots: [snapshot(0)] } }
      : path.endsWith('comparisons') ? { runs: [{ strategy: 'fifo', status: 'complete', error: null, makespan: 0 }, { strategy: 'cpsat', status: 'failed', error: 'Calcul indisponible' }, { strategy: 'cnp', status: 'incomplete', error: null, makespan: null }] } : original(path, body)
    const wrapper = await app()
    await run(wrapper)
    expect(wrapper.get('[data-testid="play"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="compare"]').trigger('click'); await flushPromises()
    const comparison = wrapper.get('[data-testid="comparison"]')
    expect(comparison.text()).toContain('CP-SAT : Calcul indisponible')
    expect(comparison.text()).toContain('Échec')
    expect(comparison.text()).toContain('Incomplet')
    const makespan = comparison.findAll('tr').find(row => row.text().includes('Makespan'))!
    expect(makespan.findAll('td').map(cell => cell.text())).toEqual(['0', '—', 'Incomplet'])
  })
})

describe('CNP and editor components', () => {
  it('renders real task states, filters, IDs, grouped endpoints and the complete legend', async () => {
    const data = inspection.contention
    const wrapper = mount(Warehouse, { props: { instance: data.instance, snapshot: data.snapshots[0]!, selected: null, participants: new Set<string>(), animate: false, duration: 500 } })
    wrappers.push(wrapper)
    const legend = wrapper.get('[aria-label="Légende de l’entrepôt"]').text()
    for (const label of ['Robot', 'sélectionné', 'Obstacle', 'Case libre', 'Collecte', 'Destination', 'ressource partagée', 'En attente', 'en cours', 'Terminée', 'Non arrivée', '45 %', '55 %']) expect(legend).toContain(label)
    expect(wrapper.findAll('[data-tasks]')).toHaveLength(2)
    expect(wrapper.get('[data-endpoint="pickup"]').attributes('data-tasks')).toBe('t1,t2')
    expect(wrapper.get('[data-endpoint="pickup"]').text()).toContain('×2')
    await wrapper.get('[data-endpoint="pickup"]').trigger('click')
    expect(wrapper.emitted('selectTask')![0]).toEqual(['t1'])
    await wrapper.get('[data-resource="pickup"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('selectResource')![0]).toEqual(['pickup'])
    await wrapper.get('[data-testid="filter-pending"]').setValue(false)
    expect(wrapper.findAll('[data-tasks]')).toHaveLength(0)
    expect(data.snapshots[0]!.tasks.t1!.state).toBe('pending')
    await wrapper.setProps({ snapshot: data.snapshots.at(-1)! })
    expect(wrapper.findAll('[data-tasks]')).toHaveLength(0)
    await wrapper.get('[data-testid="filter-completed"]').setValue(true)
    expect(wrapper.get('[data-task-state="completed"]').attributes('stroke')).toBe('#148263')
    expect(wrapper.get('[data-endpoint="pickup"] > g').attributes('opacity')).toBe('0.45')
  })

  it('uses real future, assigned and collected states and only draws the recorded route', async () => {
    const data = inspection.dynamic
    const wrapper = mount(Warehouse, { props: { instance: data.instance, snapshot: data.snapshots[0]!, selected: null, participants: new Set<string>(), animate: false, duration: 500 } })
    wrappers.push(wrapper)
    expect(wrapper.find('[data-task-state="scheduled"]').exists()).toBe(false)
    await wrapper.get('[data-testid="filter-scheduled"]').setValue(true)
    expect(wrapper.get('[data-task-state="scheduled"]').attributes('stroke-dasharray')).toBe('2 2')
    const assigned = data.snapshots.find(s => s.tick === 2)!
    await wrapper.setProps({ snapshot: assigned, selected: 'r1' })
    expect(wrapper.get('[data-task-state="assigned"]').attributes('stroke')).toBe('#2563eb')
    expect(wrapper.find('[data-testid="robot-path"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="task-relation"]').exists()).toBe(true)
    const moving = inspection.contention.snapshots.find(s => s.tick === 4)!
    await wrapper.setProps({ instance: inspection.contention.instance, snapshot: moving })
    expect(wrapper.get('[data-task-state="picked_up"]').attributes('stroke')).toBe('#2563eb')
    const center = ([x, y]: number[]) => `${x! * 64 + 32},${y! * 64 + 32}`
    expect(wrapper.get('[data-testid="robot-path"]').attributes('points')).toBe([moving.robots.r1!.position, ...moving.robots.r1!.path].map(center).join(' '))
    expect(wrapper.get('[data-testid="robot-path"]').attributes('marker-end')).toBe('url(#route-arrow)')
    expect(wrapper.get('[data-testid="task-relation"]').attributes('marker-end')).toBe('url(#relation-arrow)')
    await wrapper.setProps({ selected: null, selectedTask: 't2' })
    expect(wrapper.find('[data-testid="robot-path"]').exists()).toBe(false)
    expect(wrapper.findAll('.task-marker.focused')).toHaveLength(2)
  })

  it('keeps colocated robots inside their true cell and away from task badges', async () => {
    const data = inspection.contention
    const wrapper = mount(Warehouse, { props: { instance: data.instance, snapshot: data.snapshots[0]!, selected: null, participants: new Set<string>(), animate: false, duration: 500 } })
    wrappers.push(wrapper)
    const robots = wrapper.findAll('[data-robot]')
    expect(robots.map(r => r.attributes('data-position'))).toEqual(['0,0', '0,0'])
    expect(new Set(robots.map(r => r.attributes('transform'))).size).toBe(2)
    for (const robot of robots) {
      const [x, y] = robot.attributes('transform')!.match(/[\d.]+/g)!.map(Number)
      expect(x).toBeGreaterThan(0); expect(x).toBeLessThan(64)
      expect(y).toBeGreaterThan(20); expect(y).toBeLessThan(48)
      expect(robot.find('rect').exists()).toBe(true)
      await robot.trigger('click')
    }
    expect(wrapper.emitted('select')).toEqual([['r1'], ['r2']])
  })

  it('inspects tasks and stations from real snapshots, persists selection and uses no API during replay', async () => {
    const data = inspection.contention
    const initial = data.snapshots[0]!
    const recorded: Result = { trace: { instance: data.instance, snapshots: data.snapshots, current: data.snapshots.at(-1)!, events: data.events, strategy: 'fifo' }, metrics: {} }
    respond = path => path === '/api/examples' ? ['contention.json'] : path.endsWith('simulations') ? recorded : { instance: data.instance, snapshot: initial }
    const wrapper = await app()
    await run(wrapper)
    const calls = fetchMock.mock.calls.length
    await wrapper.get('[data-endpoint="pickup"]').trigger('click')
    expect(wrapper.get('.inspection h2').text()).toBe('Tâche t1')
    expect(wrapper.get('[data-testid="task-inspection"]').text()).toContain('En attente (pending)')
    expect(wrapper.get('[data-testid="task-inspection"]').text()).toContain('Tick 15')
    await wrapper.get('[data-testid="inspect-task"]').setValue('t2')
    await wrapper.get('[aria-label="Tick"]').setValue('2')
    expect(wrapper.get('.inspection h2').text()).toBe('Tâche t2')
    expect(wrapper.get('[data-testid="task-inspection"]').text()).toContain('Affectée / collecte (assigned)')
    expect(wrapper.findAll('.task-marker.focused')).toHaveLength(2)
    await wrapper.get('[data-robot="r2"]').trigger('click')
    expect(wrapper.get('[data-testid="inspection"]').text()).toContain('Attente de ressource à la collecte')
    expect(wrapper.get('[data-testid="inspection"]').text()).toContain('Ressource attenduepickup')
    expect(wrapper.get('[data-resource="pickup"]').classes()).toContain('focused')
    await wrapper.get('[data-resource="pickup"]').trigger('click')
    expect(wrapper.get('[data-testid="resource-inspection"]').text()).toContain('1 place(s) occupée(s) sur 1')
    expect(wrapper.get('[data-testid="resource-inspection"]').text()).toContain('Saturée')
    expect(wrapper.get('[data-testid="resource-inspection"]').text()).toContain('r1')
    await wrapper.get('[aria-label="Tick"]').setValue('3')
    expect(wrapper.get('.inspection h2').text()).toBe('Station pickup')
    expect(wrapper.get('[data-testid="resource-inspection"]').text()).toContain('0 place(s) occupée(s) sur 1')
    expect(wrapper.get('[data-testid="resource-inspection"]').text()).toContain('r2')
    await wrapper.get('[data-testid="inspect-task"]').setValue('t1')
    await wrapper.get('[aria-label="Tick"]').setValue('12')
    expect(wrapper.get('[data-testid="task-inspection"]').text()).toContain('Terminée (completed)')
    expect(wrapper.get('[data-testid="task-inspection"]').text()).toContain('Tick 12')
    expect(wrapper.findAll('.task-marker.focused')).toHaveLength(2)
    await wrapper.get('[aria-label="Recommencer"]').trigger('click')
    expect(wrapper.get('[data-testid="task-inspection"]').text()).toContain('En attente (pending)')
    expect(fetchMock).toHaveBeenCalledTimes(calls)
  })

  it('filters real CNP broadcasts, offers, refusals and contracts for the selected robot or task up to the replay tick', async () => {
    const events = inspection.dynamic.events.filter(e => e.kind === 'message')
    const wrapper = mount(EventLog, { props: { events, tick: 0, selected: 'r1', robots: ['r1', 'r2'], tasks: ['t1', 't2', 't3'], messages: true } })
    wrappers.push(wrapper)
    expect(wrapper.findAll('tbody tr').some(row => row.text().includes('announce'))).toBe(true)
    expect(wrapper.findAll('tbody tr').some(row => row.text().includes('offer'))).toBe(true)
    await wrapper.get('[data-testid="log-scope"]').setValue('past')
    await wrapper.setProps({ tick: 4 })
    expect(wrapper.findAll('tbody tr').every(row => Number(row.get('td').text()) <= 4)).toBe(true)
    expect(wrapper.findAll('tbody tr').some(row => row.text().includes('refuse'))).toBe(true)
    await wrapper.setProps({ selected: null, selectedTask: 't1' })
    expect((wrapper.get('[data-testid="log-robot"]').element as HTMLSelectElement).value).toBe('')
    expect((wrapper.get('[data-testid="log-task"]').element as HTMLSelectElement).value).toBe('t1')
    expect(wrapper.findAll('tbody tr').every(row => row.findAll('td')[4]!.text() === 't1')).toBe(true)
    expect(wrapper.findAll('tbody tr').some(row => row.text().includes('award'))).toBe(true)
    await wrapper.setProps({ tick: 0 })
    expect(wrapper.findAll('tbody tr').every(row => Number(row.get('td').text()) === 0)).toBe(true)
  })

  it('clears a robot selection only when that robot disappears from the displayed snapshot', async () => {
    const original = respond
    const changed = clone(result)
    delete changed.trace.snapshots[2]!.robots.r1
    respond = (path, body) => path.endsWith('simulations') ? changed : original(path, body)
    const wrapper = await app()
    await run(wrapper)
    await wrapper.get('[data-robot="r1"]').trigger('click')
    await wrapper.get('[aria-label="Tick"]').setValue('1')
    expect(wrapper.get('.inspection h2').text()).toBe('Agent r1')
    await wrapper.get('[aria-label="Tick"]').setValue('2')
    expect(wrapper.get('.inspection h2').text()).toBe('Vue globale')
    await wrapper.get('[aria-label="Recommencer"]').trigger('click')
    expect(wrapper.get('.inspection h2').text()).toBe('Vue globale')
  })

  it('filters messages by tick, robot, task and type; full history survives tick changes', async () => {
    const wrapper = mount(EventLog, { props: { events: events.filter(e => e.kind === 'message'), tick: 0, selected: null, robots: ['r1', 'r2'], tasks: ['t1', 'other'], messages: true } })
    wrappers.push(wrapper)
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    await wrapper.get('[data-testid="log-type"]').setValue('offer')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.text()).toContain('4')
    await wrapper.get('[data-testid="log-task"]').setValue('other')
    expect(wrapper.findAll('tbody tr')).toHaveLength(0)
    await wrapper.get('[data-testid="log-task"]').setValue('t1')
    await wrapper.get('[data-testid="log-type"]').setValue('')
    await wrapper.setProps({ selected: 'r1' })
    expect((wrapper.get('[data-testid="log-robot"]').element as HTMLSelectElement).value).toBe('r1')
    await wrapper.get('[data-testid="log-scope"]').setValue('history')
    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
    await wrapper.setProps({ tick: 3 })
    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
    expect(wrapper.get('[data-testid="log-count"]').text()).toContain('3 / 3')
  })

  it('paginates 1919 messages without losing the total', async () => {
    const wrapper = mount(EventLog, { props: { events: Array.from({ length: 1919 }, (_, tick) => ({ tick: tick % 200, kind: 'message', type: 'offer', sender: 'r1' })), tick: 0, selected: null, robots: ['r1'], tasks: [], messages: true } })
    wrappers.push(wrapper)
    await wrapper.get('[data-testid="log-scope"]').setValue('history')
    expect(wrapper.findAll('tbody tr')).toHaveLength(100)
    expect(wrapper.text()).toContain('1919 / 1919')
    await wrapper.findAll('button').at(-1)!.trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(100)
    await wrapper.setProps({ tick: 30 })
    expect(wrapper.text()).toContain('2 / 20')
    await wrapper.get('[data-testid="log-type"]').setValue('offer')
    expect(wrapper.text()).toContain('1 / 20')
  })

  it('edits every supported collection without mutating input and supports add/remove', async () => {
    const wrapper = mount(InstanceEditor, { props: { instance: clone(instance), busy: false, jsonDraft: '' } })
    wrappers.push(wrapper)
    await wrapper.get('[aria-label="Tâches 1 Échéance"]').setValue('12')
    let data = wrapper.emitted('change')!.at(-1)![0] as Instance
    expect(data.tasks[0]!.deadline).toBe(12)
    expect(instance.tasks[0]!.deadline).toBeNull()
    await wrapper.get('[aria-label="Robots 1 x"]').setValue('1')
    data = wrapper.emitted('change')!.at(-1)![0] as Instance
    expect(data.robots[0]!.position).toEqual([1, 0])
    const add = wrapper.findAll('button').find(button => button.text() === 'Ajouter Indisponibilités')!
    await add.trigger('click')
    data = wrapper.emitted('change')!.at(-1)![0] as Instance
    expect(data.unavailability).toEqual([{ robot_id: '', start_tick: 0, end_tick: 1 }])
    await wrapper.setProps({ instance: data })
    await wrapper.get('[aria-label="Supprimer Indisponibilités 1"]').trigger('click')
    expect((wrapper.emitted('change')!.at(-1)![0] as Instance).unavailability).toEqual([])
  })

  it('uses recorded availability, hides future task markers and permits keyboard selection', async () => {
    const state = snapshot(1)
    state.robots.r1!.unavailable = true
    state.tasks.t1!.state = 'scheduled'
    const wrapper = mount(Warehouse, { props: { instance, snapshot: state, selected: null, participants: new Set(['r1']), animate: false, duration: 500 } })
    wrappers.push(wrapper)
    expect(wrapper.text()).toContain('suspendu')
    expect(wrapper.text()).not.toContain('Collecte [')
    await wrapper.get('[data-robot="r1"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('select')![0]).toEqual(['r1'])
  })

  it('exports JSON through a download and revokes its object URL', async () => {
    vi.useFakeTimers()
    const create = vi.fn((_blob: Blob) => 'blob:taskfleet')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    download('taskfleet-instance.json', instance)
    expect(create).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    const blob = create.mock.calls[0]![0] as Blob
    expect(blob.type).toBe('application/json')
    await vi.runAllTimersAsync()
    expect(revoke).toHaveBeenCalledWith('blob:taskfleet')
  })
})
