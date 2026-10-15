import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import CoordinationDetails from '../components/CoordinationDetails.vue'
import MarlDetails from '../components/MarlDetails.vue'
import type { ScientificData } from '../experiments'
import type { Result } from '../types'

const coordination: ScientificData = {
  status: 'CONFIRMED', stop_reason: 'ROUND_BUDGET', domains: { r1: [null, 't1', 't2'], r2: [null, 't1'] },
  neighbors: { r1: ['r2'], r2: ['r1'] }, raw_assignments: [['r1', 't1'], ['r2', 't1']],
  assignments: [['r1', 't1']], raw_quality: { conflict_pairs: 1, estimated_cost: 6 },
  final_quality: { cardinality: 1, estimated_cost: 3, feasible: true }, confirmation_abandonments: 1,
  history: [{ round: 1, values: { r1: 't2', r2: 't1' }, changed: 1, strict_improvements: 1 }],
  messages: [
    { round: 0, type: 'domain', sender: 'r1', recipient: 'all', tasks: ['t1', 't2'] },
    { round: 1, type: 'value', sender: 'r1', recipient: 'r2', action: 't1' },
    { round: 2, type: 'claim', sender: 'r2', recipient: 'r1', action: 't1' },
    { round: 2, type: 'confirmed', sender: 'r2', recipient: 'r1', action: null },
  ],
}
const episode: ScientificData = {
  policy: 'llm', terminated: false, truncated: true, rewards: [-2, -1],
  decisions: [
    { tick: 0, observations: { r1: { tick: 0, robot: { id: 'r1' }, legal_actions: [null, 't1'] }, r2: { tick: 0, robot: { id: 'r2' }, legal_actions: [null, 't1'] } },
      proposals: { r1: 't1', r2: 't1' }, accepted: [['r1', 't1']], rejected: [], conflicts: ['r2'] },
    { tick: 1, observations: { r1: { tick: 1, robot: { id: 'r1' }, legal_actions: [null] }, r2: { tick: 1, robot: { id: 'r2' }, legal_actions: [null, 't2'] } },
      proposals: { r1: null, r2: null }, accepted: [], rejected: [], conflicts: [] },
  ],
  llm_trace: [
    { observation: { tick: 0, robot: { id: 'r1' } }, action: 't1', fallback: false, structured_valid: true, action_legal: true },
    { observation: { tick: 0, robot: { id: 'r2' } }, action: 't1', fallback: false, structured_valid: true, action_legal: true },
    { observation: { tick: 1, robot: { id: 'r2' } }, action: null, fallback: true, error: 'timeout', structured_valid: false, action_legal: false },
  ],
}

describe('distributed coordination details', () => {
  it('keeps communication rounds separate from replay ticks and displays actual before/after values', async () => {
    const wrapper = mount(CoordinationDetails, { props: { coordination, tasks: ['t1', 't2'] } })
    expect(wrapper.get('[data-testid="coordination-messages"] thead').text()).toContain('Tour')
    expect(wrapper.get('[data-testid="coordination-messages"] thead').text()).not.toContain('Tick')
    expect(wrapper.get('[data-testid="coordination-rounds"]').text()).toContain('t2')
    expect(wrapper.get('[data-testid="coordination-status"]').text()).toContain('Conflits avant confirmation : 1')
    const secondRobot = wrapper.get('[data-testid="domains"]').findAll('tbody tr')[1]!
    expect(secondRobot.text()).toContain('t1')
    expect(secondRobot.text()).toContain('Attente')
    await wrapper.get('[data-testid="coordination-round"]').setValue('1')
    expect(wrapper.get('[data-testid="log-count"]').text()).toContain('1 / 4')
    expect(wrapper.get('[data-testid="coordination-messages"] tbody').text()).toContain('t1')
    wrapper.unmount()
  })

  it('filters domain broadcasts by advertised tasks and exposes missing history without invented rounds', async () => {
    const wrapper = mount(CoordinationDetails, { props: { coordination: { ...coordination, history: [] }, tasks: ['t1', 't2'] } })
    await wrapper.get('[data-testid="log-task"]').setValue('t2')
    expect(wrapper.get('[data-testid="log-count"]').text()).toContain('1 / 4')
    expect(wrapper.get('[data-testid="coordination-messages"] tbody').text()).toContain('domain')
    expect(wrapper.text()).toContain('Aucun tour de décision exécuté')
    expect(wrapper.find('[data-testid="coordination-rounds"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('cooperative learning details', () => {
  it('aligns reward and actions with the transition ending at replay tick, then seeks through the shared cursor', async () => {
    const wrapper = mount(MarlDetails, { props: { episode, tick: 1 } })
    expect(wrapper.get('[data-testid="marl-transition"]').text()).toContain('0 vers 1')
    expect(wrapper.get('[data-testid="episode-decision"]').text()).toContain('"tick": 0')
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('Récompense -2 · Retour cumulé -2')
    expect(wrapper.get('[data-testid="marl-actions"]').text()).toContain('Conflit : priorité à un autre robot')
    const secondAgent = wrapper.get('[data-testid="marl-actions"]').findAll('tbody tr')[1]!
    expect(secondAgent.text()).toContain('Aucune nouvelle affectation')
    await wrapper.get('[data-testid="decision-tick"]').setValue('1')
    expect(wrapper.emitted('seek')).toEqual([[2]])
    await wrapper.setProps({ tick: 2 })
    expect(wrapper.get('[data-testid="marl-transition"]').text()).toContain('1 vers 2')
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('Récompense -1 · Retour cumulé -3')
    expect(wrapper.get('[data-testid="marl-actions"]').text()).toContain('Secours : timeout')
    expect(wrapper.get('[data-testid="marl-actions"]').text()).not.toContain('Décision LLM validée')
    expect(wrapper.get('[data-testid="marl-actions"]').text()).toContain('Aucun appel LLM pour cet agent')
    wrapper.unmount()
  })

  it('keeps tick zero before any reward and labels the next decision as future replay', () => {
    const wrapper = mount(MarlDetails, { props: { episode, tick: 0 } })
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('Récompense — (aucune transition) · Retour cumulé 0')
    expect(wrapper.get('[data-testid="marl-transition"]').text()).toContain('Prochaine transition, pas encore rejouée')
    expect(wrapper.findAll('.current-row')).toHaveLength(0)
    wrapper.unmount()
  })

  it('takes delivered tasks and termination from the selected snapshot, not final episode flags', () => {
    const trace = { snapshots: [{ tick: 1, all_tasks_completed: false, finished: false }],
      current: { tick: 2, all_tasks_completed: false, finished: true },
      events: [{ tick: 2, kind: 'delivered', task_id: 't1' }] } as Result['trace']
    const wrapper = mount(MarlDetails, { props: { episode, tick: 1, trace } })
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('État au tick 1 : En cours')
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('Total livré : 0')
    wrapper.unmount()
  })
})
