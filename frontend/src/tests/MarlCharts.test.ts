import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MarlDetails from '../components/MarlDetails.vue'
import MarlTraining from '../components/MarlTraining.vue'
import type { ScientificData } from '../experiments'
import type { Result } from '../types'

const episode: ScientificData = {
  policy: 'llm', rewards: [-2, 0, -1], terminated: true,
  decisions: [
    { tick: 2, observations: { r1: {}, r2: {} }, proposals: { r1: null, r2: null }, accepted: [], rejected: [], conflicts: [] },
    { tick: 0, observations: { r1: { legal_actions: [null, 't1'] }, r2: { legal_actions: [null, 't1'] } },
      proposals: { r1: 't1', r2: 't1' }, accepted: [['r1', 't1']], rejected: [], conflicts: ['r2'] },
    { tick: 1, observations: { r1: {}, r2: {} }, proposals: { r1: null, r2: 'absent' }, accepted: [], rejected: ['r2'], conflicts: [] },
  ],
  llm_trace: [
    { observation: { tick: 0, robot: { id: 'r1' } }, fallback: false },
    { observation: { tick: 0, robot: { id: 'r2' } }, fallback: true, error: 'timeout' },
  ],
}
const trace = {
  current: { tick: 3, all_tasks_completed: true, finished: false },
  snapshots: [{ tick: 0, all_tasks_completed: false, finished: false }, { tick: 1, all_tasks_completed: false, finished: false }, { tick: 2, all_tasks_completed: false, finished: false }],
  events: [{ tick: 3, kind: 'delivered', task_id: 't1', robot_id: 'r1' }],
} as Result['trace']
const training: ScientificData = {
  training_seed: 7, selected_episode: 2, validation_return: -12,
  curve: [{ episode: 3, return: -6 }, { episode: 1, return: -10 }, { episode: 2, return: -8 }],
  validation: [{ episode: 2, mean_return: -12, returns: [-14, -10] }, { episode: 3, mean_return: -11, returns: [-13, -9] }],
}

describe('MARL episode charts', () => {
  it('uses recorded transition rewards and delivery events on one tick scale', async () => {
    const wrapper = mount(MarlDetails, { props: { episode, tick: 1, trace } })
    const rewards = wrapper.findAll('[data-series="reward"] circle')
    expect(rewards.map(point => [point.attributes('data-tick'), point.attributes('data-value')])).toEqual([['1', '-2'], ['2', '0'], ['3', '-1']])
    expect(wrapper.findAll('[data-series="return"] circle').map(point => point.attributes('data-value'))).toEqual(['0', '-2', '-2', '-3'])
    expect(wrapper.findAll('[data-series="completed"] circle').map(point => point.attributes('data-value'))).toEqual(['0', '0', '0', '1'])
    expect(wrapper.get('[data-series="completed"] circle[data-tick="1"]').attributes('cx')).toBe(rewards[0]!.attributes('cx'))
    expect(wrapper.get('[data-testid="marl-curve-cursor"]').attributes('x1')).toBe(rewards[0]!.attributes('cx'))
    expect(wrapper.get('[data-testid="marl-timeline-cursor"]').attributes('x1')).toBe(rewards[0]!.attributes('cx'))
    expect(wrapper.get('[data-delivery-event]').classes()).toContain('future-event')
    await rewards[2]!.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('seek')).toEqual([[3]])
    await wrapper.setProps({ tick: 3 })
    expect(wrapper.get('[data-testid="marl-curve-cursor"]').attributes('x1')).toBe(rewards[2]!.attributes('cx'))
    expect(wrapper.get('[data-delivery-event]').classes()).not.toContain('future-event')
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('Total livré : 1')
    expect(wrapper.get('[data-testid="marl-technical-details"]').attributes('open')).toBeUndefined()
  })

  it('orders actual proposals by pre-transition tick and distinguishes accepted, rejected, conflict and provenance', async () => {
    const wrapper = mount(MarlDetails, { props: { episode, tick: 0, trace } })
    const events = wrapper.findAll('[data-action-event]')
    expect(events.map(event => event.attributes('data-tick'))).toEqual(['0', '0', '1', '1', '2', '2'])
    expect(events[0]!.attributes('data-status')).toBe('accepted')
    expect(events[0]!.attributes('data-source')).toBe('LLM')
    expect(events[1]!.attributes('data-status')).toBe('conflict')
    expect(events[1]!.attributes('data-source')).toBe('Secours')
    expect(events[3]!.attributes('data-status')).toBe('rejected')
    expect(Number(events[0]!.get('circle').attributes('cx'))).toBeLessThan(Number(events[2]!.get('circle').attributes('cx')))
    await events[1]!.trigger('click')
    expect(wrapper.emitted('seek')).toEqual([[1]])
    expect(wrapper.get('[data-testid="marl-transition"]').text()).toContain('les agents reçoivent uniquement les observations locales')
    await wrapper.get('[data-delivery-event]').trigger('click')
    expect(wrapper.emitted('seek')).toEqual([[1], [3]])
  })

  it('shows unavailable records without synthesizing rewards, deliveries or cumulative values', () => {
    const wrapper = mount(MarlDetails, { props: { episode: { rewards: [-2, null, -1] }, tick: 2 } })
    expect(wrapper.findAll('[data-series="reward"] circle').map(point => point.attributes('data-value'))).toEqual(['-2', '-1'])
    expect(wrapper.findAll('[data-series="return"] circle').map(point => point.attributes('data-value'))).toEqual(['0', '-2'])
    expect((wrapper.get('[data-series="reward"] path').attributes('d') ?? '').match(/M/g)).toHaveLength(2)
    expect(wrapper.findAll('[data-series="completed"] circle')).toHaveLength(0)
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('Retour cumulé —')
    expect(wrapper.text()).toContain('Trace moteur indisponible')
    expect(wrapper.text()).toContain('chronologie des propositions indisponible')
  })

  it('replaces curves and actions when another episode is supplied', async () => {
    const wrapper = mount(MarlDetails, { props: { episode, tick: 3, trace } })
    await wrapper.setProps({ episode: { policy: 'random', rewards: [-7], decisions: [] }, tick: 0, trace: undefined })
    expect(wrapper.findAll('[data-series="reward"] circle').map(point => point.attributes('data-value'))).toEqual(['-7'])
    expect(wrapper.findAll('[data-action-event]')).toHaveLength(0)
    expect(wrapper.findAll('[data-delivery-event]')).toHaveLength(0)
    expect(wrapper.get('[data-testid="marl-current"]').text()).toContain('aucune transition')
  })

  it('keeps a delivery and a new accepted proposal at the same robot tick on separate rows', () => {
    const wrapper = mount(MarlDetails, { props: {
      episode: { policy: 'heuristic', rewards: Array(13).fill(-1), decisions: [
        { tick: 5, observations: { r001: { legal_actions: [null, 't002'] } }, proposals: { r001: 't002' }, accepted: [['r001', 't002']], rejected: [], conflicts: [] },
      ] }, tick: 5,
      trace: { ...trace, current: { ...trace.current, tick: 13 }, events: [
        { tick: 5, kind: 'delivered', task_id: 't001', robot_id: 'r001' },
        { tick: 13, kind: 'delivered', task_id: 't002', robot_id: 'r001' },
      ] },
    } })
    const proposal = wrapper.get('[data-action-event][data-tick="5"]')
    const delivery = wrapper.get('[data-delivery-event][data-tick="5"]')
    const deliveryPosition = delivery.get('path').attributes('d')!.match(/^M ([\d.]+) ([\d.]+)/)!
    expect(Number(deliveryPosition[1])).toBe(Number(proposal.get('circle').attributes('cx')))
    expect(Number(deliveryPosition[2]) + 7).toBeGreaterThan(Number(proposal.get('circle').attributes('cy')) + 14)
    expect(Number(delivery.get('text').attributes('y'))).toBeGreaterThan(Number(proposal.get('.source-label').attributes('y')) + 12)
    expect(proposal.text()).toContain('t002')
    expect(delivery.text()).toContain('t001')
  })

  it('anchors final delivery labels and nearby proposal labels inside the right edge', () => {
    const wrapper = mount(MarlDetails, { props: {
      episode: { policy: 'q', rewards: Array(14).fill(-1), decisions: [
        { tick: 13, observations: { r1: { legal_actions: [null, 't2'] } }, proposals: { r1: 't2' }, accepted: [['r1', 't2']], rejected: [], conflicts: [] },
      ] }, tick: 14, trace: { ...trace, current: { ...trace.current, tick: 14 }, events: [
        { tick: 14, kind: 'delivered', task_id: 't1', robot_id: 'r1' },
      ] },
    } })
    const proposal = wrapper.get('[data-action-event]')
    for (const label of proposal.findAll('text')) {
      expect(label.attributes('text-anchor')).toBe('end')
      expect(Number(label.attributes('x'))).toBeLessThan(Number(proposal.get('circle').attributes('cx')))
    }
    const finalLabel = wrapper.get('[data-delivery-event] text')
    expect(finalLabel.attributes('text-anchor')).toBe('end')
    expect(Number(finalLabel.attributes('x'))).toBeLessThan(Number(wrapper.get('[data-testid="marl-timeline-cursor"]').attributes('x1')))
  })
})

describe('MARL training charts', () => {
  it('plots training and validation separately and marks the actual retained validation checkpoint', () => {
    const wrapper = mount(MarlTraining, { props: { training, checkpoint: 'session:7' } })
    expect(wrapper.findAll('[data-training-series="training"] circle').map(point => [point.attributes('data-episode'), point.attributes('data-value')])).toEqual([['1', '-10'], ['2', '-8'], ['3', '-6']])
    expect(wrapper.findAll('[data-training-series="validation"] circle').map(point => point.attributes('data-value'))).toEqual(['-12', '-11'])
    expect(wrapper.get('[data-training-series="validation"] polyline').attributes('stroke-dasharray')).toBe('6 4')
    expect(wrapper.get('[data-testid="marl-selected-checkpoint"]').attributes('data-episode')).toBe('2')
    expect(wrapper.get('[data-testid="marl-single-seed"]').text()).toContain('Une seule graine')
    expect(wrapper.text()).toContain('test reste une évaluation figée distincte')
  })

  it('compares only supplied training seeds and never derives training from a checkpoint', async () => {
    const wrapper = mount(MarlTraining, { props: { training: [training, { ...training, training_seed: 11 }] } })
    expect(wrapper.findAll('[data-training-series="training"]').map(row => row.attributes('data-seed'))).toEqual(['7', '11'])
    expect(wrapper.find('[data-testid="marl-single-seed"]').exists()).toBe(false)
    await wrapper.setProps({ training: undefined, checkpoint: 'historical-q' })
    expect(wrapper.find('[data-testid="marl-training-curves"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="marl-no-training"]').text()).toContain('épisode d’évaluation seul ne fournit pas un historique')
    expect(wrapper.text()).toContain('historical-q')
  })

  it('does not invent a validation point for a retained episode absent from the recorded history', () => {
    const wrapper = mount(MarlTraining, { props: { training: { ...training, validation: [] } } })
    expect(wrapper.find('[data-testid="marl-selected-checkpoint"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Aucun résultat de validation enregistré')
    expect(wrapper.text()).toContain('Point de validation retenu indisponible')
  })
})
