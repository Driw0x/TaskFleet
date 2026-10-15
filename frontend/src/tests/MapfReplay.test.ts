import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MapfReplay from '../components/MapfReplay.vue'
import type { MapfProblem, ScientificData } from '../experiments'

const problem: MapfProblem = { width: 3, height: 2, horizon: 5, obstacles: [[2, 1]], agents: [
  { id: 'a', start: [0, 0], goal: [1, 0] },
  { id: 'b', start: [2, 0], goal: [2, 0] },
] }
const plan: ScientificData = { status: 'SOLVED', paths: { a: [[0, 0], [0, 0], [1, 0]], b: [[2, 0]] },
  validation: { valid: true, violations: [] } }

describe('MAPF replay semantics', () => {
  it('distinguishes starting, waiting, movement and persistent goal occupation at one logical tick', async () => {
    const wrapper = mount(MapfReplay, { props: { problem, plan, tick: 0 } })
    expect(wrapper.get('[data-mapf-agent="a"]').text()).toContain('Départ')
    await wrapper.setProps({ tick: 1 })
    expect(wrapper.get('[data-mapf-agent="a"] [data-position]').attributes('data-waiting')).toBe('true')
    expect(wrapper.get('[data-mapf-agent="b"] [data-position]').attributes('data-waiting')).toBe('false')
    await wrapper.setProps({ tick: 5 })
    expect(wrapper.get('[data-mapf-agent="a"] [data-position]').attributes('data-position')).toBe('1,0')
    expect(wrapper.get('[data-mapf-agent="a"] [data-position]').attributes('data-waiting')).toBe('false')
    expect(wrapper.get('[data-mapf-agent="b"] [data-position]').attributes('data-position')).toBe('2,0')
    expect(wrapper.text()).toContain('occupation persistante')
  })

  it('renders all robot markers above paths and allows inspecting an agent', async () => {
    const wrapper = mount(MapfReplay, { props: { problem, plan, tick: 1 } })
    const layers = wrapper.findAll('svg > g').map(group => group.attributes('data-layer'))
    expect(layers.indexOf('robots')).toBeGreaterThan(layers.indexOf('paths'))
    await wrapper.get('[aria-label="Inspecter agent a"]').trigger('click')
    expect(wrapper.get('[data-testid="mapf-inspection"]').text()).toContain('Attentes avant arrivée : 1')
    expect(wrapper.get('[data-testid="mapf-inspection"]').text()).toContain('Déplacements : 1')
  })

  it('does not report movement or arrival without a plan', () => {
    const wrapper = mount(MapfReplay, { props: { problem, plan: { paths: null, status: 'BUDGET_EXHAUSTED' }, tick: 0 } })
    expect(wrapper.text()).toContain('Aucun plan vérifié disponible')
    expect(wrapper.text()).not.toContain('Destination atteinte')
    expect(wrapper.get('[data-mapf-agent="a"]').text()).toContain('Départ · aucun plan')
  })

  it('accepts a verified empty plan without claiming search failure', () => {
    const wrapper = mount(MapfReplay, { props: { problem: { ...problem, agents: [] }, plan: { paths: {}, validation: { valid: true } }, tick: 0 } })
    expect(wrapper.text()).not.toContain('Aucun plan vérifié disponible')
  })
})
