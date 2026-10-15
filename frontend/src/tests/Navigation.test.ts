import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import App from '../App.vue'
import inspection from './fixtures/inspection.json'

let wrapper: VueWrapper | undefined
const captured = inspection.contention
const trace = { ...captured, strategy: 'fifo', current: captured.snapshots.at(-1)! }
const catalog = { warehouse_instances: [{ id: 'm7.json', label: 'm7.json', instance: captured.instance }],
  mapf_instances: [{ id: 'crossing.json', label: 'crossing.json', instance: { width: 2, height: 2, horizon: 3, obstacles: [], agents: [{ id: 'a', start: [0, 0], goal: [1, 0] }] } }],
  checkpoints: [], scenario_sets: [], training_sets: [], validation_sets: [], selected_models: {}, limits: {} }
beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.stubGlobal('fetch', vi.fn(async (path: string) => ({ ok: true, json: async () => {
    if (path === '/api/examples') return ['contention.json']
    if (path.startsWith('/api/examples/')) return { instance: captured.instance, snapshot: captured.snapshots[0] }
    if (path === '/api/v2/catalog') return catalog
    if (path === '/api/v2/jobs') return { id: 'complete', status: 'completed', result: {
      milestone: 'm7', method: 'nominal', execution_status: 'valid', metrics: {}, trace,
    } }
    throw Error(`Unexpected endpoint ${path}`)
  } })))
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/') })
async function open(url = '/') {
  window.history.replaceState(null, '', url)
  wrapper = mount(App); await flushPromises()
  return wrapper
}
async function traverse(direction: 'back' | 'forward') {
  await new Promise<void>(resolve => {
    window.addEventListener('popstate', () => resolve(), { once: true })
    window.history[direction]()
  })
  await flushPromises()
}

describe('native browser navigation', () => {
  it.each([
    ['ordonnancement', 'm7', 'nominal'], ['allocation-distribuee', 'm8', 'dsa'],
    ['apprentissage-cooperatif', 'm10', 'random'],
  ])('initializes the correct default method for a direct %s URL', async (slug, milestone, method) => {
    const app = await open(`/?view=experiences&problem=${slug}`)
    expect(app.get('[data-testid="milestone"]').element).toHaveProperty('value', milestone)
    expect(app.get('[data-testid="method"]').element).toHaveProperty('value', method)
  })
  it('opens and reloads a direct scientific problem URL', async () => {
    const app = await open('/?view=experiences&problem=planification-multi-agent')
    expect(app.get('[data-testid="experiments"]').isVisible()).toBe(true)
    expect(app.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm9')
    expect(app.get('[data-testid="method"]').text()).toContain('CBS')
    expect(app.get('[data-testid="method"]').element).toHaveProperty('value', 'prioritized')
    const url = window.location.href
    app.unmount(); wrapper = undefined
    const reloaded = await open(url)
    expect(reloaded.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm9')
    expect(reloaded.get('[data-testid="method"]').element).toHaveProperty('value', 'prioritized')
    expect(reloaded.get('[data-testid="v2-instance"]').element).toHaveProperty('value', 'crossing.json')
  })

  it('uses back and forward to restore views and problems without adding history entries', async () => {
    const app = await open()
    const push = vi.spyOn(window.history, 'pushState')
    await app.get('[data-testid="nav-v2"]').trigger('click'); await flushPromises()
    expect(window.location.search).toBe('?view=experiences&problem=ordonnancement')
    await app.get('[data-testid="milestone"]').setValue('m9'); await flushPromises()
    expect(window.location.search).toBe('?view=experiences&problem=planification-multi-agent')
    expect(push).toHaveBeenCalledTimes(2)
    await traverse('back')
    expect(app.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm7')
    await traverse('back')
    expect(app.get('[data-testid="strategy"]').isVisible()).toBe(true)
    await traverse('forward'); await traverse('forward')
    expect(app.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm9')
    expect(push).toHaveBeenCalledTimes(2)
  })

  it('preserves a result when returning to the same problem and does not record replay ticks', async () => {
    const app = await open('/?view=experiences&problem=ordonnancement')
    await app.get('[data-testid="experiment-form"]').trigger('submit'); await flushPromises()
    expect(app.find('[data-testid="v2-result"]').exists()).toBe(true)
    const push = vi.spyOn(window.history, 'pushState')
    await app.get('[data-testid="experiments"] [aria-label="Tick suivant"]').trigger('click')
    expect(push).not.toHaveBeenCalled()
    await app.get('[data-testid="nav-v1"]').trigger('click')
    await traverse('back')
    expect(app.get('[data-testid="experiments"]').isVisible()).toBe(true)
    expect(app.find('[data-testid="v2-result"]').exists()).toBe(true)
    expect(push).toHaveBeenCalledTimes(1)
    await app.get('[data-testid="milestone"]').setValue('m9'); await flushPromises()
    expect(app.find('[data-testid="v2-result"]').exists()).toBe(false)
  })

  it('normalizes an unknown problem with replaceState and keeps unrelated URL parameters', async () => {
    const push = vi.spyOn(window.history, 'pushState')
    const app = await open('/?view=experiences&problem=absent&keep=yes')
    expect(app.get('[data-testid="milestone"]').element).toHaveProperty('value', 'm7')
    expect(new URL(window.location.href).searchParams.get('problem')).toBe('ordonnancement')
    expect(new URL(window.location.href).searchParams.get('keep')).toBe('yes')
    expect(push).not.toHaveBeenCalled()
  })
})
