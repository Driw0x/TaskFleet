import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Warehouse from '../components/Warehouse.vue'
import MapfReplay from '../components/MapfReplay.vue'
import type { Instance, Snapshot } from '../types'

const instance: Instance = { width: 3, height: 1, horizon: 10, seed: null, obstacles: [], resources: [], unavailability: [],
  robots: [{ id: 'r1', position: [0, 0], capacity: 1 }],
  tasks: [{ id: 't1', pickup: [1, 0], destination: [2, 0], load: 1, handling_ticks: 1, deadline: 5, arrival_tick: 0 }] }
const snapshot: Snapshot = { tick: 0, finished: false, all_tasks_completed: false,
  robots: { r1: { position: [0, 0], capacity: 1, state: 'idle', available: true, unavailable: false, task_id: null,
    handling_remaining: 0, resource_id: null, path: [] } },
  tasks: { t1: { state: 'pending', robot_id: null, arrival_tick: 0, completed_tick: null, late: false } }, resources: {} }

describe('adaptive scientific grids', () => {
  it('caps a three-cell warehouse and retains readable cells on larger grids', async () => {
    const wrapper = mount(Warehouse, { props: { instance, snapshot, selected: null, participants: new Set<string>(), animate: false, duration: 500 } })
    const svg = wrapper.get('svg').element as SVGElement
    expect(svg.style.maxWidth).toBe('285px')
    expect(svg.style.minWidth).toBe('228px')
    expect(wrapper.get('svg').attributes('viewBox')).toBe('-18 -18 228 100')
    await wrapper.setProps({ instance: { ...instance, width: 20, height: 12 } })
    expect(svg.style.maxWidth).toBe('1645px')
    expect(svg.style.minWidth).toBe('1316px')
    expect(wrapper.get('svg').attributes('viewBox')).toBe('-18 -18 1316 804')
    expect(wrapper.get('[data-robot="r1"]').attributes('data-position')).toBe('0,0')
    wrapper.unmount()
  })

  it('keeps the visible legend relevant while preserving full help and task filters', async () => {
    const wrapper = mount(Warehouse, { props: { instance, snapshot, selected: null, participants: new Set<string>(), animate: false, duration: 500 } })
    const legend = wrapper.get('[data-testid="warehouse-legend-primary"]')
    expect(legend.text()).toContain('Robot')
    expect(legend.text()).toContain('Collecte')
    expect(legend.text()).toContain('Destination')
    expect(legend.text()).not.toContain('Station')
    expect(legend.text()).not.toContain('Obstacle')
    expect(legend.text()).not.toContain('CNP')
    const help = wrapper.get('[aria-label="Légende de l’entrepôt"] details')
    expect(help.attributes('open')).toBeUndefined()
    expect(help.text()).toContain('Les collisions physiques ne sont pas simulées')
    await wrapper.get('[data-testid="filter-pending"]').setValue(false)
    expect(wrapper.find('[data-tasks]').exists()).toBe(false)
    expect(legend.text()).not.toContain('Collecte')
    await wrapper.setProps({ snapshot: { ...snapshot, resources: { station: { position: [1, 0], capacity: 1, holders: ['r1'] } } } })
    expect(legend.text()).toContain('Station saturée')
    wrapper.unmount()
  })

  it('caps small MAPF grids and preserves useful scale for a large problem', async () => {
    const wrapper = mount(MapfReplay, { props: { problem: { width: 3, height: 1, horizon: 10, obstacles: [],
      agents: [{ id: 'a', start: [0, 0], goal: [2, 0] }] }, plan: { paths: { a: [[0, 0], [1, 0], [2, 0]] } }, tick: 1 } })
    const svg = wrapper.get('svg').element as SVGElement
    expect(svg.style.maxWidth).toBe('255px')
    expect(svg.style.minWidth).toBe('204px')
    expect(wrapper.get('[data-mapf-agent="a"] [data-position]').attributes('data-position')).toBe('1,0')
    await wrapper.setProps({ problem: { width: 20, height: 12, horizon: 10, obstacles: [], agents: [] } })
    expect(svg.style.maxWidth).toBe('1445px')
    expect(svg.style.minWidth).toBe('1156px')
    wrapper.unmount()
  })
})
