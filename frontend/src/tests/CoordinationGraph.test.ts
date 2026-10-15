import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import CoordinationDetails from '../components/CoordinationDetails.vue'
import type { ScientificData } from '../experiments'

const coordination: ScientificData = {
  method: 'dsa', status: 'CONFIRMED', stop_reason: 'ROUND_BUDGET',
  domains: { r1: [null, 't1', 't2'], r2: [null, 't1', 't2'], r3: [null] },
  raw_assignments: [['r1', 't2'], ['r2', 't2']], assignments: [['r1', 't2']],
  raw_quality: { conflict_pairs: 1 }, final_quality: { cardinality: 1 }, confirmation_abandonments: 1,
  history: [
    { round: 1, values: { r1: 't1', r2: 't1', r3: null } },
    { round: 2, values: { r1: 't2', r2: 't2', r3: null } },
  ],
  messages: [
    { round: 0, type: 'domain', sender: 'r1', recipient: 'all', tasks: ['t1', 't2'] },
    { round: 0, type: 'domain', sender: 'r2', recipient: 'all', tasks: ['t1', 't2'] },
    { round: 1, type: 'value', sender: 'r1', recipient: 'r2', action: 't2' },
    { round: 1, type: 'value', sender: 'r2', recipient: 'r1', action: 't1' },
    { round: 2, type: 'value', sender: 'r1', recipient: 'r2', action: 't1' },
    { round: 2, type: 'value', sender: 'r2', recipient: 'r1', action: 't1' },
    { round: 3, type: 'claim', sender: 'r1', recipient: 'r2', action: 't2' },
    { round: 3, type: 'claim', sender: 'r2', recipient: 'r1', action: 't2' },
    { round: 3, type: 'confirmed', sender: 'r1', recipient: 'r2', action: 't2' },
    { round: 3, type: 'confirmed', sender: 'r2', recipient: 'r1', action: null },
  ],
}

describe('coordination graph', () => {
  it('shows confirmed assignments, abandoned conflicts and idle robots before collapsed technical tables', async () => {
    const wrapper = mount(CoordinationDetails, { props: { coordination, tasks: ['t1', 't2'] } })
    const graph = wrapper.get('[data-testid="coordination-graph"]')
    expect(graph.findAll('[data-state="confirmed"]')).toHaveLength(1)
    expect(graph.get('[data-state="confirmed"]').attributes('data-task')).toBe('t2')
    expect(graph.get('[data-state="abandoned"]').attributes('data-robot')).toBe('r2')
    expect(graph.get('[data-state="abandoned"]').attributes('data-conflict')).toBe('true')
    expect(graph.get('[data-coordination-robot="r2"]').text()).toContain('Sans affectation')
    expect(graph.get('[data-coordination-robot="r3"]').attributes('data-unassigned')).toBe('true')
    expect(graph.findAll('[data-state="compatible"]')).toHaveLength(0)
    expect(wrapper.get('[data-testid="coordination-technical"]').attributes('open')).toBeUndefined()
    await wrapper.get('[data-testid="coordination-compatibility"]').setValue(true)
    expect(graph.findAll('[data-state="compatible"]')).toHaveLength(4)
    await graph.get('[data-state="confirmed"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.get('[data-testid="coordination-inspection"]').text()).toContain('Robot r1')
    expect(wrapper.get('[data-testid="coordination-inspection"]').text()).toContain('Tâche t2')
    wrapper.unmount()
  })

  it('navigates real rounds using post-update choices while messages keep their pre-update values', async () => {
    const wrapper = mount(CoordinationDetails, { props: { coordination, tasks: ['t1', 't2'] } })
    const graph = wrapper.get('[data-testid="coordination-graph"]')
    await wrapper.get('[data-testid="coordination-step"]').setValue('0')
    expect(graph.findAll('[data-state="confirmed"]')).toHaveLength(0)
    expect(graph.findAll('[data-state="conflict"]')).toHaveLength(2)
    expect(graph.findAll('[data-state="conflict"]').every(link => link.attributes('data-task') === 't1')).toBe(true)
    expect(wrapper.get('[data-testid="coordination-stage"]').text()).toContain('Tour 1')
    expect(wrapper.get('[data-testid="coordination-stage"]').text()).toContain('Messages logiques : 4 / 10')
    expect(wrapper.get('[data-testid="coordination-exchanges"]').text()).toContain('t2')
    expect(wrapper.get('[data-testid="coordination-exchanges"]').text()).toContain('avant mise à jour locale')
    await wrapper.get('[aria-label="Tour suivant"]').trigger('click')
    expect(graph.findAll('[data-state="conflict"]').every(link => link.attributes('data-task') === 't2')).toBe(true)
    expect(graph.findAll('[data-state="abandoned"]')).toHaveLength(2)
    expect(wrapper.get('[data-testid="coordination-stage"]').text()).toContain('Messages logiques : 6 / 10')
    await wrapper.get('[aria-label="Tour suivant"]').trigger('click')
    expect(graph.findAll('[data-state="confirmed"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="coordination-stage"]').text()).toContain('Messages logiques : 10 / 10')
    wrapper.unmount()
  })

  it('shows only known final data without inventing history or compatibility for other methods', () => {
    const wrapper = mount(CoordinationDetails, { props: {
      coordination: { method: 'cnp', assignments: [['r1', 't1']] }, tasks: ['t1', 'future'],
    } })
    expect(wrapper.find('[data-testid="coordination-step"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-state="confirmed"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-state="abandoned"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('Seuls les choix finaux enregistrés')
    wrapper.unmount()
  })

  it('distinguishes model choices from fallback and keeps a rejected raw proposal out of confirmed links', async () => {
    const llm = { ...coordination, method: 'llm',
      raw_assignments: [['r1', 't1']], assignments: [['r1', 't1']],
      history: [
        { round: 1, values: { r1: 't1', r2: 't1', r3: null } },
        { round: 2, values: { r1: 't1', r2: null, r3: null } },
      ],
      llm_trace: [
        { observation: { robot: { id: 'r1' } }, action: 't1', fallback: false, structured_valid: true, action_legal: true },
        { observation: { robot: { id: 'r2' } }, action: 't1', fallback: false, structured_valid: true, action_legal: true },
        { observation: { robot: { id: 'r1' } }, action: 't1', fallback: false, structured_valid: true, action_legal: true },
        { observation: { robot: { id: 'r2' } }, action: null, fallback: true, error: 'invalid_action', raw_content: '{"action":"unknown"}' },
      ],
    }
    const wrapper = mount(CoordinationDetails, { props: { coordination: llm, tasks: ['t1', 't2'] } })
    expect(wrapper.get('[data-testid="coordination-llm-sources"]').text()).toContain('Secours : invalid_action')
    expect(wrapper.get('[data-testid="coordination-llm-sources"]').text()).toContain('Proposition brute rejetée : unknown')
    expect(wrapper.findAll('[data-state="confirmed"]').map(link => link.attributes('data-robot'))).toEqual(['r1'])
    expect(wrapper.find('[data-coordination-task="unknown"]').exists()).toBe(false)
    await wrapper.get('[data-testid="coordination-step"]').setValue('0')
    expect(wrapper.get('[data-testid="coordination-llm-sources"]').text()).not.toContain('Secours')
    expect(wrapper.get('[data-testid="coordination-llm-sources"]').text()).toContain('Décision LLM validée')
    wrapper.unmount()
  })

  it('resets round, selection, filters and compatibility when instance results change', async () => {
    const wrapper = mount(CoordinationDetails, { props: { coordination, tasks: ['t1', 't2'] } })
    await wrapper.get('[data-testid="coordination-step"]').setValue('0')
    await wrapper.get('[data-testid="coordination-compatibility"]').setValue(true)
    await wrapper.get('[data-coordination-robot="r1"]').trigger('click')
    await wrapper.get('[data-testid="log-robot"]').setValue('r1')
    await wrapper.setProps({ coordination: { domains: { other: [null, 'new'] }, assignments: [['other', 'new']] }, tasks: ['new'] })
    expect(wrapper.find('[data-testid="coordination-step"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="coordination-inspection"]').exists()).toBe(false)
    expect(wrapper.find('[data-coordination-robot="r1"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-state="compatible"]')).toHaveLength(0)
    expect(wrapper.get('[data-state="confirmed"]').attributes('data-task')).toBe('new')
    expect((wrapper.get('[data-testid="log-robot"]').element as HTMLSelectElement).value).toBe('')
    wrapper.unmount()
  })
})
