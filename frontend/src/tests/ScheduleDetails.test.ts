import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ScheduleDetails from '../components/ScheduleDetails.vue'
import type { ScientificResult } from '../experiments'
import type { Instance, Result, Snapshot } from '../types'

const instance: Instance = { width: 3, height: 2, horizon: 6, seed: null, obstacles: [], unavailability: [],
  robots: [{ id: 'r', position: [0, 0], capacity: 1 }],
  resources: [{ id: 'station', position: [0, 0], capacity: 1 }],
  tasks: [{ id: 'task', pickup: [0, 0], destination: [1, 0], load: 1, handling_ticks: 2, deadline: 4, arrival_tick: 0 }] }
const state = (tick: number): Snapshot => ({ tick, finished: tick === 6, all_tasks_completed: tick >= 6,
  robots: { r: { position: tick >= 4 ? [1, 0] : [0, 0], capacity: 1, state: tick === 0 || tick === 6 ? 'idle' : tick >= 4 ? 'delivering' : 'picking',
    available: tick === 0 || tick === 6, unavailable: tick === 2, task_id: tick === 0 || tick === 6 ? null : 'task',
    handling_remaining: tick === 0 || tick === 6 ? 0 : 1, resource_id: tick === 1 || tick === 2 ? 'station' : null, path: [] } },
  tasks: { task: { state: tick === 0 ? 'pending' : tick < 3 ? 'assigned' : tick < 6 ? 'picked_up' : 'completed',
    robot_id: tick === 0 ? null : 'r', arrival_tick: 0, completed_tick: tick === 6 ? 6 : null, late: tick === 6 } },
  resources: { station: { position: [0, 0], capacity: 1, holders: tick === 1 || tick === 2 ? ['r'] : [] } } })
const trace: Result['trace'] = { instance, strategy: 'fifo', current: state(6), snapshots: [0, 1, 2, 3, 4, 5, 6].map(state), events: [
  { tick: 0, kind: 'assigned', robot_id: 'r', task_id: 'task' },
  { tick: 1, kind: 'resource_acquired', robot_id: 'r', resource_id: 'station' },
  { tick: 2, kind: 'robot_suspended', robot_id: 'r', task_id: 'task', resource_id: 'station' },
  { tick: 3, kind: 'collected', robot_id: 'r', task_id: 'task', resource_id: 'station' },
  { tick: 3, kind: 'resource_released', robot_id: 'r', resource_id: 'station' },
  { tick: 4, kind: 'moved', robot_id: 'r', task_id: 'task', from_position: [0, 0], position: [1, 0] },
  { tick: 6, kind: 'delivered', robot_id: 'r', task_id: 'task' },
] }
const plan = { start_tick: 0, horizon: 6, status: 'OPTIMAL', optimal: true, objective: [1, 5],
  assignments: [{ tick: 0, robot_id: 'r', task_id: 'task' }], tasks: { task: { collected_tick: 2, completed_tick: 5 } } }
const nominal: ScientificResult = { milestone: 'm7', method: 'nominal', execution_status: 'valid', metrics: {}, plan, execution: { executable: false } }

describe('schedule timeline and causal replay', () => {
  it('positions recorded milestones on one tick axis and keeps their DOM order chronological', async () => {
    const wrapper = mount(ScheduleDetails, { props: { result: nominal, trace, tick: 1 } })
    const collection = '[data-event-task="task"][data-event-stage="collection"]'
    expect(wrapper.get(`${collection}[data-event-kind="planned"]`).attributes('transform')).toBe('translate(370 108)')
    expect(wrapper.find(`${collection}[data-event-kind="actual"]`).exists()).toBe(false)
    expect(wrapper.get('[data-testid="schedule-cursor"]').attributes('transform')).toBe('translate(280 0)')
    expect(wrapper.get('[data-testid="schedule-technical-details"]').attributes('open')).toBeUndefined()
    await wrapper.setProps({ tick: 6 })
    expect(wrapper.get(`${collection}[data-event-kind="actual"]`).attributes('transform')).toBe('translate(460 129)')
    expect(wrapper.get('[data-testid="schedule-cursor"]').attributes('transform')).toBe('translate(730 0)')
    const dates = wrapper.findAll('[data-event-tick]').map(event => Number(event.attributes('data-event-tick')))
    expect(dates).toEqual([...dates].sort((a, b) => a - b))
    expect(wrapper.findAll('[data-operation-tick]').every(operation => Number(operation.attributes('data-operation-tick')) <= 6)).toBe(true)
    await wrapper.setProps({ tick: 1 })
    expect(wrapper.findAll('[data-event-kind="actual"]').map(event => Number(event.attributes('data-event-tick')))).toEqual([0])
    expect(wrapper.find('[data-event-stage="completion"][data-event-kind="planned"]').classes()).toContain('schedule-future')
  })

  it('selects milestones, emits the shared replay tick and clears inspection with another result', async () => {
    const wrapper = mount(ScheduleDetails, { props: { result: nominal, trace, tick: 1 } })
    await wrapper.get('[data-event-stage="completion"][data-event-kind="planned"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('seek')).toEqual([[5]])
    expect(wrapper.get('[data-testid="schedule-inspection"]').text()).toContain('Livraison prévue au tick 5')
    expect(wrapper.get('[data-testid="schedule-inspection"]').text()).toContain('réalisée : Non réalisée')
    await wrapper.get('[data-axis-tick="3"]').trigger('click')
    expect(wrapper.emitted('seek')?.at(-1)).toEqual([3])
    await wrapper.setProps({ result: { ...nominal, plan: { ...plan, tasks: {} } }, trace: undefined })
    expect(wrapper.find('[data-testid="schedule-inspection"]').exists()).toBe(false)
    expect(wrapper.find('[data-event-tick]').exists()).toBe(false)
  })

  it('places tasks of different robots by their own dates instead of robot order', () => {
    const result: ScientificResult = { ...nominal, plan: { ...plan,
      assignments: [{ tick: 4, robot_id: 'a', task_id: 'later' }, { tick: 1, robot_id: 'z', task_id: 'earlier' }],
      tasks: { later: { collected_tick: 5, completed_tick: 6 }, earlier: { collected_tick: 2, completed_tick: 3 } },
    } }
    const wrapper = mount(ScheduleDetails, { props: { result, tick: 0 } })
    const events = wrapper.findAll('[data-event-tick]')
    expect(events.map(event => event.attributes('data-event-task'))).toEqual(['earlier', 'earlier', 'earlier', 'later', 'later', 'later'])
    expect(events[0]?.attributes('transform')).toBe('translate(280 184)')
    expect(events[3]?.attributes('transform')).toBe('translate(550 108)')
  })

  it('keeps predicted and observed dates distinct, then reports actual deviation and lateness', async () => {
    const wrapper = mount(ScheduleDetails, { props: { result: nominal, trace, tick: 1 } })
    expect(wrapper.get('[data-task="task"] [data-stage="collection"]').text()).toContain('Prévu : tick 2Réalisé : Non réalisée')
    expect(wrapper.get('[data-task="task"] [data-stage="completion"]').text()).toContain('Prévu : tick 5Réalisé : Non réalisée')
    expect(wrapper.find('[data-testid="task-deviation"]').exists()).toBe(false)
    expect(wrapper.get('[data-task="task"]').classes()).toContain('current-row')
    await wrapper.setProps({ tick: 6 })
    expect(wrapper.get('[data-task="task"] [data-stage="collection"]').text()).toContain('Prévu : tick 2Réalisé : tick 3')
    expect(wrapper.get('[data-task="task"] [data-stage="completion"]').text()).toContain('Prévu : tick 5Réalisé : tick 6')
    expect(wrapper.get('[data-testid="task-deviation"]').text()).toContain('+1 tick(s) entre fin réalisée 6 et fin prévue 5')
    expect(wrapper.get('[data-testid="task-tardiness"]').text()).toContain('Retard réel : 2 tick(s) · fin 6, échéance 4')
    expect(wrapper.get('[data-task="task"]').text()).toContain('Échéance dépassée')
  })

  it('shows real reservations through suspension and never equates spatial presence with occupancy', async () => {
    const wrapper = mount(ScheduleDetails, { props: { result: nominal, trace, tick: 0 } })
    expect(wrapper.get('[data-testid="schedule-resources"]').text()).toContain('Libre')
    expect(wrapper.find('[data-testid="schedule-reservations"]').exists()).toBe(false)
    await wrapper.setProps({ tick: 2 })
    expect(wrapper.get('[data-testid="schedule-resources"] tbody').text()).toContain('r')
    expect(wrapper.get('[data-testid="schedule-reservations"]').text()).toContain('Réservation conservée')
    expect(wrapper.get('[data-testid="schedule-operations"]').text()).toContain('Suspension')
    expect(wrapper.get('[data-testid="schedule-operations"]').text()).not.toContain('station : attente')
    expect(wrapper.get('[data-testid="schedule-operations"]').text()).not.toContain('Collecte terminée')
    await wrapper.setProps({ tick: 4 })
    expect(wrapper.get('[data-testid="schedule-resources"]').text()).toContain('Libre')
    expect(wrapper.get('[data-testid="schedule-reservations"] tbody').text()).toContain('3')
    expect(wrapper.get('[data-testid="schedule-operations"]').text()).toContain('Déplacement vers livraison')
    expect(wrapper.get('[data-testid="schedule-operations"]').text()).not.toContain('station : attente')
  })

  it('reveals rolling plans, commitments, changes and FIFO dispatches only when known', async () => {
    const result: ScientificResult = { ...nominal, method: 'rolling', plan: undefined, rolling: {
      plans: [plan, { ...plan, start_tick: 2, assignments: [], committed: [{ robot_id: 'r', task_id: 'task' }],
        tasks: { task: { collected_tick: 3, completed_tick: 6 } } },
      { ...plan, start_tick: 5, status: 'UNKNOWN', objective: null, tasks: { unseen: { completed_tick: 999 } } }],
      dispatches: [{ tick: 0, robot_id: 'r', task_id: 'task', source: 'plan', plan_start_tick: 0 },
        { tick: 5, robot_id: 'other', task_id: 'unseen', source: 'fifo', plan_start_tick: 5 }],
    } }
    const wrapper = mount(ScheduleDetails, { props: { result, trace, tick: 1 } })
    expect(wrapper.get('[data-testid="replans"] tbody').findAll('tr')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('unseen')
    expect(wrapper.text()).not.toContain('999')
    await wrapper.setProps({ tick: 2 })
    expect(wrapper.get('[data-testid="replans"] tbody').findAll('tr')).toHaveLength(2)
    expect(wrapper.get('[aria-label="Calendrier du robot r"] [data-task="task"]').text()).toContain('task')
    expect(wrapper.text()).toContain('Dates modifiées')
    expect(wrapper.text()).toContain('Engagements actifs gelés : r / task')
    expect(wrapper.get('[data-testid="schedule-dispatches"] tbody').text()).not.toContain('Secours FIFO')
    expect(wrapper.findAll('[data-plan-tick]').map(marker => Number(marker.attributes('data-plan-tick')))).toEqual([0, 2])
    expect(wrapper.get('[data-event-stage="collection"][data-event-kind="planned"]').attributes('data-event-tick')).toBe('3')
    await wrapper.get('[data-testid="schedule-plan-picker"]').setValue('0')
    expect(wrapper.get('[data-event-stage="collection"][data-event-kind="planned"]').attributes('data-event-tick')).toBe('2')
    await wrapper.get('[data-plan-tick="2"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.get('[data-event-stage="collection"][data-event-kind="planned"]').attributes('data-event-tick')).toBe('3')
    await wrapper.setProps({ tick: 5 })
    expect(wrapper.get('[data-testid="schedule-dispatches"] tbody').text()).toContain('Secours FIFO')
    expect(wrapper.get('[data-testid="replans"]').text()).toContain('UNKNOWN')
  })

  it('returns to the latest known rolling plan after rewinding before a selected plan', async () => {
    const result: ScientificResult = { ...nominal, method: 'rolling', plan: undefined, rolling: {
      plans: [plan, { ...plan, start_tick: 2, tasks: { task: { collected_tick: 3, completed_tick: 6 } } },
        { ...plan, start_tick: 4, tasks: { task: { collected_tick: 4, completed_tick: 7 } } }],
    } }
    const wrapper = mount(ScheduleDetails, { props: { result, trace, tick: 2 } })
    const picker = wrapper.get<HTMLSelectElement>('[data-testid="schedule-plan-picker"]')
    await picker.setValue('2')
    await wrapper.setProps({ tick: 1 })
    expect(picker.element.selectedIndex).toBe(0)
    expect(wrapper.get('[data-testid="schedule-status"]').text()).toContain('plan établi au tick 0')
    expect(wrapper.find('[data-plan-tick="2"]').exists()).toBe(false)
    await wrapper.setProps({ tick: 4 })
    expect(picker.element.selectedIndex).toBe(0)
    expect(wrapper.get('[data-testid="schedule-status"]').text()).toContain('plan établi au tick 4')
    expect(wrapper.get('[data-event-stage="completion"][data-event-kind="planned"]').attributes('data-event-tick')).toBe('7')
  })

  it('clears a selected planned milestone when a different rolling plan becomes visible', async () => {
    const result: ScientificResult = { ...nominal, method: 'rolling', plan: undefined, rolling: {
      plans: [plan, { ...plan, start_tick: 2, tasks: { task: { collected_tick: 3, completed_tick: 6 } } },
        { ...plan, start_tick: 4, tasks: { task: { collected_tick: 4, completed_tick: 7 } } }],
    } }
    const wrapper = mount(ScheduleDetails, { props: { result, trace, tick: 2 } })
    const collection = '[data-event-stage="collection"][data-event-kind="planned"]'
    await wrapper.get(collection).trigger('click')
    expect(wrapper.get('[data-testid="schedule-inspection"]').text()).toContain('Collecte prévue au tick 3')
    const picker = wrapper.get<HTMLSelectElement>('[data-testid="schedule-plan-picker"]')
    await picker.setValue('0')
    expect(wrapper.find('[data-testid="schedule-inspection"]').exists()).toBe(false)
    expect(wrapper.get(collection).attributes('data-event-tick')).toBe('2')
    await picker.setValue(picker.element.options[0]!.value)
    await wrapper.get(collection).trigger('click')
    expect(wrapper.find('[data-testid="schedule-inspection"]').exists()).toBe(true)
    await wrapper.setProps({ tick: 4 })
    expect(wrapper.find('[data-testid="schedule-inspection"]').exists()).toBe(false)
    expect(wrapper.get(collection).attributes('data-event-tick')).toBe('4')
  })

  it('keeps robust scenario execution separate and shows declared guarantee boundaries', async () => {
    const result: ScientificResult = { milestone: 'm7', method: 'robust', execution_status: 'valid', metrics: {},
      robust: { status: 'OPTIMAL', objective: [2, 6], policy: { r: ['task'] }, policies_evaluated: 3, scenarios: [[], [{ robot_id: 'r', start_tick: 2, end_tick: 3 }]] },
      scenario_runs: [{ execution_status: 'valid', metrics: { tasks_completed: 1, total_tardiness: 0, makespan: 5 } },
        { execution_status: 'valid', metrics: { tasks_completed: 1, total_tardiness: 2, makespan: 6 } }] }
    const wrapper = mount(ScheduleDetails, { props: { result, trace, tick: 2, scenarioIndex: 1 } })
    expect(wrapper.get('[data-testid="robust-scenarios"] .current-row').text()).toContain('2 · rejoué')
    expect(wrapper.text()).toContain('hors ensemble ne sont pas garantis')
    expect(wrapper.get('[data-task="task"] [data-stage="completion"]').text()).toContain('Prévu : Non disponibleRéalisé : Non réalisée')
    expect(wrapper.find('[data-event-kind="planned"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="robust-timeline-scope"]').text()).toContain('scénario 2 rejoué uniquement')
    await wrapper.setProps({ tick: 6 })
    expect(wrapper.get('[data-task="task"] [data-stage="completion"]').text()).toContain('Réalisé : tick 6')
  })

  it('keeps current operation visible with tick changes while technical execution details stay collapsed', async () => {
    const wrapper = mount(ScheduleDetails, { props: { result: nominal, trace, tick: 0 } })
    expect(wrapper.get('[data-testid="schedule-status"]').text()).toContain('Solution optimale prouvée')
    expect(wrapper.get('[data-testid="schedule-execution-details"]').attributes('open')).toBeUndefined()
    expect(wrapper.get('[data-testid="schedule-current-operation"]').text()).toContain('Affectation enregistrée')
    await wrapper.setProps({ tick: 2 })
    expect(wrapper.get('[data-testid="schedule-current-tick"]').text()).toBe('Lecture au tick 2')
    expect(wrapper.get('[data-testid="schedule-current-operation"]').text()).toContain('Suspension')
    expect(wrapper.get('[data-task="task"] [data-stage="collection"]').text()).toContain('Réalisé : Non réalisée')
    await wrapper.setProps({ tick: 3 })
    expect(wrapper.get('[data-testid="schedule-current-operation"]').text()).toContain('Collecte terminée')
    expect(wrapper.get('[data-task="task"] [data-stage="collection"]').text()).toContain('Réalisé : tick 3')
    await wrapper.setProps({ tick: 6 })
    expect(wrapper.get('[data-testid="schedule-current-operation"]').text()).toContain('Livraison terminée')
    await wrapper.setProps({ tick: 1 })
    expect(wrapper.get('[data-task="task"] [data-stage="completion"]').text()).toContain('Réalisé : Non réalisée')
    expect(wrapper.find('[data-testid="task-tardiness"]').exists()).toBe(false)
  })

  it('distinguishes unavailable execution dates from events not yet observed', () => {
    const wrapper = mount(ScheduleDetails, { props: { result: nominal, tick: 0 } })
    expect(wrapper.get('[data-task="task"] [data-stage="collection"]').text()).toContain('Prévu : tick 2Réalisé : Non disponible')
    expect(wrapper.text()).toContain('« Non réalisée » : événement absent jusqu’au tick choisi')
    expect(wrapper.text()).toContain('« Non disponible » : donnée non fournie')
  })

  it('preserves fixed robust task order when no dispatch has occurred', () => {
    const result: ScientificResult = { ...nominal, method: 'robust', plan: undefined,
      robust: { status: 'OPTIMAL', policy: { r: ['z-first', 'a-second'] } } }
    const wrapper = mount(ScheduleDetails, { props: { result, tick: 0 } })
    expect(wrapper.findAll('[data-task]').map(row => row.attributes('data-task'))).toEqual(['z-first', 'a-second'])
    expect(wrapper.text()).not.toContain('Prévu : tick')
    expect(wrapper.findAll('.schedule-missing').map(marker => marker.text())).toEqual(['Dates non disponibles', 'Dates non disponibles'])
  })
})
