<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { data, display, list } from '../experiments'
import type { ScientificData, ScientificResult } from '../experiments'
import type { Result, Snapshot } from '../types'
import { robotLabels, taskLabels } from '../types'

const props = withDefaults(defineProps<{
  result: ScientificResult; trace?: Result['trace']; tick: number; scenarioIndex?: number
}>(), { scenarioIndex: 0 })
const emit = defineEmits<{ seek: [tick: number] }>()
const selectedPlan = ref<number | null>(null)
const selected = ref<{ task: string; label: string; tick?: number } | null>(null)
watch([() => props.result, () => props.trace], () => { selectedPlan.value = null; selected.value = null })
watch(() => props.tick, tick => {
  if (selectedPlan.value !== null && selectedPlan.value > tick) selectedPlan.value = null
  if (selected.value?.tick !== undefined && selected.value.tick > tick) selected.value = null
})
const events = computed(() => props.trace?.events.filter(event => event.tick <= props.tick) ?? [])
const states = computed(() => {
  const snapshots = new Map<number, Snapshot>()
  for (const state of props.trace?.snapshots ?? []) if (state.tick <= props.tick) snapshots.set(state.tick, state)
  if (props.trace && props.trace.current.tick <= props.tick) snapshots.set(props.trace.current.tick, props.trace.current)
  return [...snapshots.values()].sort((a, b) => a.tick - b.tick)
})
const snapshot = computed(() => states.value.find(state => state.tick === props.tick))
const plans = computed(() => (props.result.rolling ? list(props.result.rolling.plans).map(data) : props.result.plan ? [props.result.plan] : [])
  .filter(plan => Number(plan.start_tick ?? 0) <= props.tick))
const plan = computed(() => plans.value.find(item => item.start_tick === selectedPlan.value) ?? plans.value.at(-1))
watch(plan, () => { selected.value = null })
const dispatches = computed(() => list(props.result.rolling?.dispatches).map(data).filter(row => Number(row.tick) <= props.tick))
function assigned(taskId: string) { return events.value.find(event => event.kind === 'assigned' && event.task_id === taskId) }
function actualTick(taskId: string, kind: string) { return events.value.find(event => event.kind === kind && event.task_id === taskId)?.tick }
const rows = computed(() => {
  const taskIds = new Set([...Object.keys(data(plan.value?.tasks)), ...events.value.flatMap(event => event.task_id ? [event.task_id] : [])])
  if (props.result.robust) for (const queue of Object.values(data(props.result.robust.policy))) for (const id of list(queue)) taskIds.add(String(id))
  return [...taskIds].map(id => {
    const dates = data(data(plan.value?.tasks)[id])
    const assignment = list(plan.value?.assignments).map(data).find(action => action.task_id === id)
    const actual = assigned(id)
    const dispatch = dispatches.value.find(row => row.task_id === id)
    const completed = actualTick(id, 'delivered')
    const expected = typeof dates.completed_tick === 'number' ? dates.completed_tick : undefined
    const previous = plans.value[plans.value.indexOf(plan.value!) - 1]
    const prior = data(data(previous?.tasks)[id])
    const robot = actual?.robot_id ?? assignment?.robot_id
      ?? Object.entries(data(props.result.robust?.policy)).find(([, queue]) => list(queue).includes(id))?.[0]
    const status = snapshot.value?.tasks[id]?.state
    const metadata = props.trace?.instance.tasks.find(task => task.id === id)
    const queue = list(data(props.result.robust?.policy)[String(robot)])
    return { id, robot, plannedRobot: assignment?.robot_id ?? robot, plannedAt: plan.value?.start_tick, dispatch: assignment?.tick, order: queue.indexOf(id),
      collect: dates.collected_tick, complete: dates.completed_tick, actualDispatch: actual?.tick,
      actualCollect: actualTick(id, 'collected'), actualComplete: completed,
      delta: completed !== undefined && expected !== undefined ? completed - expected : undefined,
      overdue: expected !== undefined && props.tick > expected && completed === undefined,
      late: metadata?.deadline !== null && metadata?.deadline !== undefined
        && (completed ?? props.tick) > metadata.deadline && (actual !== undefined || status === 'pending'),
      deadline: metadata?.deadline, arrival: metadata?.arrival_tick,
      tardiness: completed !== undefined && metadata?.deadline !== undefined && metadata.deadline !== null
        ? Math.max(0, completed - metadata.deadline) : undefined,
      active: status === 'assigned' || status === 'picked_up', status: status ? taskLabels[status] : 'Prévue',
      changed: previous && id in data(previous.tasks) && (prior.collected_tick !== dates.collected_tick || prior.completed_tick !== dates.completed_tick),
      source: dispatch?.source === 'fifo' ? 'Secours FIFO' : dispatch?.source === 'plan' ? 'Plan' : undefined }
  }).sort((a, b) => String(a.robot ?? '').localeCompare(String(b.robot ?? ''))
    || (props.result.robust ? a.order - b.order : 0)
    || Number(a.dispatch ?? a.actualDispatch ?? Infinity) - Number(b.dispatch ?? b.actualDispatch ?? Infinity) || a.id.localeCompare(b.id))
})
const calendars = computed(() => [...new Set(rows.value.map(row => row.robot))].map(robot => ({
  robot, tasks: rows.value.filter(row => row.robot === robot),
})))
const operations = computed(() => states.value.filter(state => state.tick > 0).flatMap(state => Object.entries(state.robots).flatMap(([id, robot]) => {
  const current = events.value.filter(event => event.tick === state.tick && event.robot_id === id)
  const taskEvent = current.find(event => ['moved', 'collected', 'delivered', 'waiting'].includes(event.kind))
  const taskId = taskEvent?.task_id ?? robot.task_id
  if (!taskId) return []
  const collected = events.value.some(event => event.kind === 'collected' && event.task_id === taskId && event.tick < state.tick)
  const operation = robot.unavailable ? 'Suspension'
    : current.some(event => event.kind === 'moved') ? `Déplacement vers ${collected ? 'livraison' : 'collecte'}`
    : current.some(event => event.kind === 'waiting') ? 'Attente de station'
    : current.some(event => event.kind === 'collected') ? 'Collecte terminée'
    : current.some(event => event.kind === 'delivered') ? 'Livraison terminée'
    : robot.state === 'picking' ? 'Manutention de collecte'
    : robot.state === 'delivering' ? 'Manutention de livraison' : undefined
  if (!operation) return []
  const resources = current.filter(event => event.resource_id && ['resource_acquired', 'resource_released', 'waiting'].includes(event.kind))
    .map(event => `${event.resource_id} : ${event.kind === 'resource_acquired' ? 'acquise' : event.kind === 'resource_released' ? 'libérée' : 'attente'}`)
  if (robot.resource_id) resources.push(`${robot.resource_id} : réservation conservée`)
  return [{ tick: state.tick, robot: id, task: taskId, operation, resources: resources.join(' ; ') || '—' }]
})))
const reservations = computed(() => events.value.filter(event => event.kind === 'resource_acquired').map(event => ({
  ...event, released: events.value.find(other => other.kind === 'resource_released' && other.robot_id === event.robot_id
    && other.resource_id === event.resource_id && other.tick >= event.tick)?.tick,
})))
const currentOperations = computed(() => [
  ...operations.value.filter(operation => operation.tick === props.tick),
  ...events.value.filter(event => event.tick === props.tick && event.kind === 'assigned'
    && !operations.value.some(operation => operation.tick === props.tick && operation.task === event.task_id))
    .map(event => ({ tick: event.tick, robot: event.robot_id, task: event.task_id, operation: 'Affectation enregistrée', resources: '' })),
])
const colors = ['#126e7d', '#845fb7', '#ad611b', '#2162b8', '#a82a3f', '#148263']
const timelineRows = computed(() => rows.value.map((row, index) => ({ ...row, color: colors[index % colors.length],
  y: 108 + index * 76,
})))
type Milestone = { task: string; robot: unknown; stage: string; label: string; tick: number; y: number; color: string; actual: boolean }
const milestones = computed(() => timelineRows.value.flatMap(row => [
  { stage: 'assignment', label: 'Affectation', planned: row.dispatch, actual: row.actualDispatch },
  { stage: 'collection', label: 'Collecte', planned: row.collect, actual: row.actualCollect },
  { stage: 'completion', label: 'Livraison', planned: row.complete, actual: row.actualComplete },
].flatMap(stage => [false, true].flatMap(actual => {
  const tick = actual ? stage.actual : stage.planned
  return typeof tick === 'number' && Number.isFinite(tick) ? [{ task: row.id, robot: actual ? row.robot : row.plannedRobot,
    stage: stage.stage, label: stage.label, tick, y: row.y + (actual ? 21 : 0), color: row.color, actual }] : []
}))).sort((a, b) => a.tick - b.tick || a.task.localeCompare(b.task) || Number(a.actual) - Number(b.actual)))
const maximum = computed(() => Math.max(1, props.tick, props.trace?.instance.horizon ?? 0,
  Number(plan.value?.horizon ?? 0), ...milestones.value.map(event => event.tick)))
const plotWidth = computed(() => Math.max(540, Math.min(1600, maximum.value * 38)))
const chartWidth = computed(() => 190 + plotWidth.value + 32)
const chartHeight = computed(() => Math.max(178, 100 + timelineRows.value.length * 76))
const x = (tick: number) => 190 + tick / maximum.value * plotWidth.value
const axisTicks = computed(() => {
  const step = Math.max(1, Math.ceil(maximum.value / 18))
  const ticks = Array.from({ length: Math.floor(maximum.value / step) + 1 }, (_, index) => index * step)
  if (ticks.at(-1) !== maximum.value) ticks.push(maximum.value)
  return ticks
})
const inspected = computed(() => rows.value.find(row => row.id === selected.value?.task))
function inspect(event: Milestone) {
  selected.value = { task: event.task, label: `${event.label} ${event.actual ? 'réalisée' : 'prévue'} au tick ${event.tick}`,
    tick: event.actual ? event.tick : undefined }
  seek(event.tick)
}
function seek(tick: number) { if (props.trace) emit('seek', Math.max(0, Math.min(tick, props.trace.current.tick))) }
function seekChart(event: MouseEvent) {
  if (!props.trace) return
  const svg = event.currentTarget as SVGSVGElement
  const point = (event.clientX - svg.getBoundingClientRect().left) / svg.getBoundingClientRect().width * chartWidth.value
  seek(Math.round((point - 190) / plotWidth.value * maximum.value))
}
function phaseY(task: string) { return timelineRows.value.find(row => row.id === task)?.y }
function phaseColor(task: string) { return timelineRows.value.find(row => row.id === task)?.color }
function operationAt(taskId: string, robotId: unknown) {
  const current = currentOperations.value.find(operation => operation.task === taskId)
  if (current) return current.operation
  const robot = snapshot.value?.robots[String(robotId)]
  return robot?.task_id === taskId ? robot.unavailable ? 'Suspension' : robotLabels[robot.state] ?? robot.state : undefined
}
function plannedDate(value: unknown) { return value === undefined || value === null ? 'Non disponible' : `tick ${display(value)}` }
function actualDate(value: unknown) { return value === undefined || value === null ? props.trace ? 'Non réalisée' : 'Non disponible' : `tick ${display(value)}` }
function solverStatus(value: unknown) {
  const labels: Record<string, string> = {
    OPTIMAL: 'Solution optimale prouvée', FEASIBLE: 'Solution trouvée, optimalité non prouvée',
    INFEASIBLE: 'Aucune solution dans le domaine étudié', UNKNOWN: 'Recherche sans conclusion',
    MODEL_INVALID: 'Modèle invalide',
  }
  const code = display(value)
  return labels[code] ?? (code === '—' ? 'Non disponible' : code)
}
const commitments = computed(() => list(plan.value?.committed).map(data))
const scenarioRuns = computed(() => props.result.scenario_runs ?? [])
const scenarios = computed(() => list(props.result.robust?.scenarios))
function changeText(current: ScientificData, previous: ScientificData | undefined) {
  if (!previous) return 'Plan initial'
  const changes = Object.entries(data(current.tasks)).filter(([id, value]) => {
    const before = data(data(previous.tasks)[id]), after = data(value)
    return !Object.hasOwn(data(previous.tasks), id) || before.collected_tick !== after.collected_tick || before.completed_tick !== after.completed_tick
  }).map(([id]) => id)
  return changes.length ? changes.join(', ') : 'Dates inchangées'
}
</script>

<template>
  <div data-testid="schedule-details">
    <div class="schedule-heading"><h3>Calendrier chronologique global</h3><output data-testid="schedule-current-tick">Lecture au tick {{ tick }}</output></div>
    <p class="schedule-status" data-testid="schedule-status">{{ solverStatus(plan?.status ?? result.robust?.status) }}.
      <span v-if="plan">Prévisions du plan établi au tick {{ display(plan.start_tick) }}.</span>
    </p>
    <p class="hint">Temps commun à tous les robots. Jalons du solveur en haut de chaque ligne ; exécution connue en bas. Cliquer un jalon ou un tick pour synchroniser le rejeu.</p>
    <p v-if="result.robust" class="hint" data-testid="robust-timeline-scope">Calendrier du scénario {{ scenarioIndex + 1 }} rejoué uniquement. Les files robustes ne fournissent aucune date prévue ; les résultats agrégés restent dans les détails.</p>
    <label v-if="result.rolling && plans.length" class="schedule-plan-picker">Plan affiché
      <select v-model="selectedPlan" aria-label="Plan affiché" data-testid="schedule-plan-picker"><option :value="null">Dernier plan connu</option><option v-for="(item, index) in plans" :key="index" :value="Number(item.start_tick)">Plan {{ index + 1 }} · tick {{ display(item.start_tick) }}</option></select>
    </label>
    <div v-if="currentOperations.length" class="schedule-current" data-testid="schedule-current-operation" aria-live="polite">
      <strong>Au tick {{ tick }}</strong><span v-for="(operation, index) in currentOperations" :key="index">{{ operation.robot }} / {{ operation.task }} : {{ operation.operation }}</span>
    </div>
    <div v-if="rows.length" class="schedule-chart-scroll" tabindex="0" aria-label="Défilement du calendrier">
      <svg data-testid="schedule-timeline" class="schedule-chart" :viewBox="`0 0 ${chartWidth} ${chartHeight}`" :style="{ width: `${chartWidth}px` }" role="group" aria-label="Calendrier chronologique en ticks" @click="seekChart">
        <title>Jalons prévus et réalisés des tâches, au tick {{ tick }}</title>
        <rect x="190" y="45" :width="plotWidth" :height="chartHeight - 63" fill="#f7fafc" />
        <g v-for="mark in axisTicks" :key="mark" class="schedule-axis" role="button" :tabindex="trace ? 0 : -1" :aria-label="`Rejouer au tick ${mark}`" :data-axis-tick="mark" @click.stop="seek(mark)" @keydown.enter.prevent="seek(mark)" @keydown.space.prevent="seek(mark)">
          <line :x1="x(mark)" y1="45" :x2="x(mark)" :y2="chartHeight - 18" />
          <rect :x="x(mark) - 13" y="23" width="26" height="23" fill="transparent" />
          <text :x="x(mark)" y="38" text-anchor="middle">{{ mark }}</text>
        </g>
        <text x="188" y="16">Ticks de simulation</text>
        <g v-if="result.rolling" data-testid="schedule-plan-markers">
          <text x="12" y="70">Plans enregistrés</text>
          <g v-for="(item, index) in plans" :key="index" role="button" tabindex="0" :aria-label="`Afficher plan ${index + 1}, tick ${display(item.start_tick)}`" :data-plan-tick="item.start_tick" @click.stop="selectedPlan = Number(item.start_tick)" @keydown.enter.prevent="selectedPlan = Number(item.start_tick)" @keydown.space.prevent="selectedPlan = Number(item.start_tick)">
            <path :d="`M ${x(Number(item.start_tick))} 57 l 6 7 l -6 7 l -6 -7 Z`" :fill="item === plan ? '#2162b8' : '#d8e4ef'" stroke="#2162b8" />
            <title>Plan {{ index + 1 }}, tick {{ item.start_tick }} : {{ changeText(item, plans[index - 1]) }}</title>
          </g>
        </g>
        <g v-for="row in timelineRows" :key="row.id" :data-lane-task="row.id" :class="{ 'schedule-active-lane': row.active, 'schedule-selected-lane': inspected?.id === row.id }">
          <rect class="schedule-lane-background" x="0" :y="row.y - 17" :width="chartWidth" height="65" fill="transparent" />
          <g role="button" tabindex="0" :aria-label="`Inspecter ${row.id}, robot ${display(row.robot)}`" @click.stop="selected = { task: row.id, label: 'Tâche sélectionnée' }" @keydown.enter.prevent="selected = { task: row.id, label: 'Tâche sélectionnée' }" @keydown.space.prevent="selected = { task: row.id, label: 'Tâche sélectionnée' }">
            <text x="12" :y="row.y - 2" class="schedule-task-label" :fill="row.color">{{ row.id.length > 19 ? `${row.id.slice(0, 17)}…` : row.id }}</text>
            <text x="12" :y="row.y + 17">Robot {{ display(row.robot).slice(0, 21) }}</text>
            <text x="12" :y="row.y + 33" class="schedule-operation-label">{{ (operationAt(row.id, row.robot) ?? row.status).slice(0, 25) }}</text>
            <title>{{ row.id }} · robot {{ display(row.robot) }} · {{ operationAt(row.id, row.robot) ?? row.status }}{{ row.plannedRobot !== row.robot ? ` · robot prévu ${row.plannedRobot}` : '' }}</title>
          </g>
          <text x="166" :y="row.y + 4" class="schedule-track-label">P</text><text x="166" :y="row.y + 25" class="schedule-track-label">R</text>
          <line x1="190" :y1="row.y" :x2="x(maximum)" :y2="row.y" stroke="#dce5ed" />
          <line x1="190" :y1="row.y + 21" :x2="x(maximum)" :y2="row.y + 21" stroke="#dce5ed" />
          <g v-if="typeof row.deadline === 'number' && row.deadline <= maximum" :data-deadline-task="row.id">
            <line :x1="x(row.deadline)" :y1="row.y - 12" :x2="x(row.deadline)" :y2="row.y + 34" stroke="#b3263e" stroke-dasharray="3 3" />
            <path :d="`M ${x(row.deadline)} ${row.y + 34} l -4 6 h 8 Z`" fill="#b3263e" />
            <title>{{ row.id }} : échéance tick {{ row.deadline }}</title>
          </g>
          <line v-if="typeof row.deadline === 'number' && typeof row.complete === 'number' && row.complete > row.deadline" :x1="x(row.deadline)" :x2="x(row.complete)" :y1="row.y - 11" :y2="row.y - 11" stroke="#b3263e" stroke-width="2" stroke-dasharray="3 3"><title>{{ row.id }} : retard prévu {{ row.complete - row.deadline }} tick(s)</title></line>
          <line v-if="row.late && typeof row.deadline === 'number'" :x1="x(row.deadline)" :x2="x(row.actualComplete ?? tick)" :y1="row.y + 39" :y2="row.y + 39" stroke="#b3263e" stroke-width="4"><title>{{ row.id }} : retard réel {{ (row.actualComplete ?? tick) - row.deadline }} tick(s)</title></line>
          <line v-if="row.delta !== undefined && typeof row.complete === 'number' && row.actualComplete !== undefined && row.delta !== 0" :x1="x(row.complete)" :x2="x(row.actualComplete)" :y1="row.y + 10" :y2="row.y + 10" stroke="#ad611b" stroke-dasharray="3 2" stroke-width="2"><title>Écart de livraison au plan : {{ row.delta }} tick(s)</title></line>
          <text v-if="!milestones.some(event => event.task === row.id)" x="198" :y="row.y + 4" class="schedule-missing">Dates non disponibles</text>
        </g>
        <g v-for="(phase, index) in operations.filter(phase => phaseY(phase.task) !== undefined)" :key="`phase-${index}`" :data-operation-tick="phase.tick" :class="{ 'schedule-current-phase': phase.tick === tick }">
          <rect :x="x(Math.max(0, phase.tick - 1))" :y="Number(phaseY(phase.task)) + 17" :width="Math.max(2, x(phase.tick) - x(Math.max(0, phase.tick - 1)))" height="8" :fill="phaseColor(phase.task)" opacity=".5" />
          <title>{{ phase.robot }} / {{ phase.task }} · tick {{ phase.tick }} : {{ phase.operation }} · {{ phase.resources }}</title>
        </g>
        <g v-for="(event, index) in milestones" :key="`${event.task}-${event.stage}-${event.actual}`" class="schedule-milestone" :class="{ 'schedule-future': !event.actual && event.tick > tick, 'schedule-past-plan': !event.actual && event.tick < tick, 'schedule-current-event': event.tick === tick }" role="button" tabindex="0" :aria-label="`${event.task}, robot ${display(event.robot)}, ${event.label} ${event.actual ? 'réalisée' : 'prévue'}, tick ${event.tick}`" :data-milestone-index="index" :data-event-task="event.task" :data-event-stage="event.stage" :data-event-tick="event.tick" :data-event-kind="event.actual ? 'actual' : 'planned'" :transform="`translate(${x(event.tick)} ${event.y})`" @click.stop="inspect(event)" @keydown.enter.prevent="inspect(event)" @keydown.space.prevent="inspect(event)">
          <circle r="9" :fill="event.actual ? event.color : 'white'" :stroke="event.color" stroke-width="2" :stroke-dasharray="event.actual ? undefined : '3 2'" />
          <text y="3.5" text-anchor="middle" :fill="event.actual ? 'white' : event.color">{{ event.stage === 'assignment' ? 'A' : event.stage === 'collection' ? 'C' : 'L' }}</text>
          <title>{{ event.task }} · robot {{ display(event.robot) }} · {{ event.label }} {{ event.actual ? 'réalisée' : 'prévue' }} au tick {{ event.tick }}</title>
        </g>
        <g class="schedule-cursor" data-testid="schedule-cursor" :data-tick="tick" :transform="`translate(${x(tick)} 0)`" pointer-events="none"><line y1="44" :y2="chartHeight - 16" stroke="#172f47" stroke-width="2" /><path d="M -5 43 H 5 L 0 49 Z" fill="#172f47" /><text x="5" y="16">Tick {{ tick }}</text></g>
      </svg>
    </div>
    <div v-if="rows.length" class="schedule-legend"><span>○ P : prévu · ● R : réalisé</span><span>A affectation · C collecte · L livraison</span><span>Trait coloré : opération réelle observée</span><span class="schedule-late-key">Repère rouge : échéance · trait rouge : retard</span></div>
    <p v-if="rows.length" class="hint">Aucune phase de déplacement ou d’attente prévue n’est exportée. Les bandes réalisées décrivent le tick observé ; aucune durée manquante n’est déduite.</p>
    <div v-if="inspected" class="schedule-inspection" data-testid="schedule-inspection" aria-live="polite">
      <strong>{{ inspected.id }} · robot {{ display(inspected.robot) }}</strong><span>{{ selected?.label }}</span>
      <span v-if="inspected.plannedRobot !== inspected.robot">Robot prévu par ce plan : {{ display(inspected.plannedRobot) }}.</span>
      <span>Affectation — prévue : {{ plannedDate(inspected.dispatch) }} ; réalisée : {{ actualDate(inspected.actualDispatch) }}.</span>
      <span>Collecte — prévue : {{ plannedDate(inspected.collect) }} ; réalisée : {{ actualDate(inspected.actualCollect) }}.</span>
      <span>Livraison — prévue : {{ plannedDate(inspected.complete) }} ; réalisée : {{ actualDate(inspected.actualComplete) }}.</span>
      <span>Échéance : {{ plannedDate(inspected.deadline) }}. <template v-if="inspected.tardiness !== undefined">Retard réel : {{ inspected.tardiness }} tick(s).</template><template v-if="inspected.delta !== undefined"> Écart au plan : {{ inspected.delta }} tick(s).</template></span>
    </div>
    <details class="schedule-technical" data-testid="schedule-technical-details"><summary>Détails du calendrier</summary>
    <div v-if="rows.length" class="robot-calendars" data-testid="schedule-table">
      <section v-for="calendar in calendars" :key="String(calendar.robot)" class="robot-calendar" :aria-label="`Calendrier du robot ${display(calendar.robot)}`">
        <h4>{{ calendar.robot === undefined ? 'Robot non disponible' : `Robot ${calendar.robot}` }}</h4>
        <ol class="schedule-tasks">
          <li v-for="row in calendar.tasks" :key="row.id" class="schedule-task" :class="{ 'current-row': row.active }" :data-task="row.id">
            <div class="schedule-task-heading"><strong>{{ row.id }}</strong><span>{{ operationAt(row.id, row.robot) ?? row.status }}</span><span v-if="row.changed" class="badge">Dates modifiées</span><span v-if="row.source" class="badge">{{ row.source }}</span></div>
            <dl class="schedule-dates">
              <div data-stage="assignment"><dt>Affectation</dt><dd><span>Prévu : {{ plannedDate(row.dispatch) }}</span><span>Réalisé : {{ actualDate(row.actualDispatch) }}</span></dd></div>
              <div data-stage="collection"><dt>Collecte</dt><dd><span>Prévu : {{ plannedDate(row.collect) }}</span><span>Réalisé : {{ actualDate(row.actualCollect) }}</span></dd></div>
              <div data-stage="completion"><dt>Livraison / fin</dt><dd><span>Prévu : {{ plannedDate(row.complete) }}</span><span>Réalisé : {{ actualDate(row.actualComplete) }}</span></dd></div>
            </dl>
            <p v-if="row.tardiness !== undefined" class="schedule-deadline" data-testid="task-tardiness">{{ row.tardiness ? `Retard réel : ${row.tardiness} tick(s)` : 'Échéance respectée' }} · fin {{ row.actualComplete }}, échéance {{ row.deadline }}.</p>
            <p v-else-if="row.late" class="schedule-deadline">Échéance dépassée de {{ tick - Number(row.deadline) }} tick(s) ; livraison non réalisée.</p>
            <p v-else-if="row.deadline !== undefined && row.deadline !== null" class="schedule-deadline">Échéance : tick {{ row.deadline }}<span v-if="typeof row.complete === 'number' && row.complete > row.deadline"> · retard prévu : {{ row.complete - row.deadline }} tick(s)</span>.</p>
            <p v-if="row.delta !== undefined" class="schedule-deadline" data-testid="task-deviation">Écart au plan : {{ row.delta > 0 ? '+' : '' }}{{ row.delta }} tick(s) entre fin réalisée {{ row.actualComplete }} et fin prévue {{ row.complete }}.</p>
            <p v-else-if="row.overdue" class="schedule-deadline">Prévision de fin dépassée ; livraison non réalisée.</p>
            <span v-if="row.late" class="badge">Échéance dépassée</span>
          </li>
        </ol>
      </section>
    </div>
    <p v-else class="hint">Aucune tâche prévue ou exécutée disponible à ce tick.</p>
    <details class="schedule-help"><summary>Comprendre les dates et les prévisions</summary>
      <p class="hint">Statut brut du solveur : {{ display(plan?.status ?? result.robust?.status) }}.</p>
      <p class="hint">« Non réalisée » : événement absent jusqu’au tick choisi. « Non disponible » : donnée non fournie. L’affectation est la décision de confier la tâche ; la livraison marque sa fin.</p>
      <p class="hint">Arrivées : {{ rows.map(row => `${row.id} au tick ${display(row.arrival)}`).join(' ; ') || 'non disponibles' }}. Le retard réel vaut le maximum entre zéro et la fin réalisée moins l’échéance ; l’écart au plan compare les deux dates de fin.</p>
      <p class="hint">Phases de déplacement et réservations prévues non exportées par le solveur ; opérations et réservations ci-dessous proviennent de l’exécution.
        <span v-if="result.rolling">Les plans enregistrés jusqu’au tick choisi peuvent être sélectionnés ; les replanifications futures restent masquées.</span>
        <span v-if="plan">Horizon du plan : {{ display(plan.horizon) }} ; optimalité prouvée : {{ plan.optimal === true ? 'Oui' : 'Non' }}.</span>
        <span v-if="result.execution">Rejeu vérifié : {{ result.execution.executable === true ? 'Oui' : 'Non' }}.</span>
      </p>
    </details>

    <details v-if="result.rolling"><summary>Replanifications et décisions appliquées</summary>
      <h3>Décisions connues au tick {{ tick }}</h3>
      <p>Replanifications : {{ plans.length }}. Décisions de secours FIFO : {{ result.rolling.dispatches === undefined ? 'détail indisponible' : `${dispatches.filter(row => row.source === 'fifo').length} connues` }}.</p>
      <div class="table-scroll"><table data-testid="replans"><thead><tr><th>Tick</th><th>Statut / objectif</th><th>Changements depuis plan précédent</th><th>Engagements conservés</th></tr></thead><tbody><tr v-for="(item, index) in plans" :key="index" :class="{ 'current-row': item === plan }"><td>{{ display(item.start_tick) }}</td><td>{{ solverStatus(item.status) }} ({{ display(item.status) }}) / {{ display(item.objective) }}</td><td>{{ changeText(item, plans[index - 1]) }}</td><td>{{ list(item.committed).map(value => { const commitment = data(value); return `${commitment.robot_id} / ${commitment.task_id}` }).join(', ') || '—' }}</td></tr></tbody></table></div>
      <p v-if="commitments.length" class="hint">Engagements actifs gelés : {{ commitments.map(row => `${row.robot_id} / ${row.task_id}`).join(', ') }}.</p>
      <div v-if="dispatches.length" class="table-scroll"><table data-testid="schedule-dispatches"><thead><tr><th>Tick</th><th>Robot / tâche</th><th>Décision appliquée</th><th>Plan connu</th></tr></thead><tbody><tr v-for="(row, index) in dispatches" :key="index"><td>{{ display(row.tick) }}</td><td>{{ display(row.robot_id) }} / {{ display(row.task_id) }}</td><td>{{ row.source === 'fifo' ? 'Secours FIFO' : 'Plan' }}</td><td>{{ display(row.plan_start_tick) }}</td></tr></tbody></table></div>
    </details>

    <details v-if="result.robust"><summary>Scénarios et garantie de la politique robuste</summary>
      <h3>Files fixes robustes</h3>
      <p>Pire retard / pire makespan : {{ display(result.robust.objective) }}. {{ display(result.robust.policies_evaluated) }} politiques évaluées.</p>
      <p class="hint">Garantie limitée aux scénarios déclarés et aux files fixes énumérées. Les scénarios hors ensemble ne sont pas garantis. Tous scénarios déclarés doivent achever toutes tâches dans l’horizon.</p>
      <div class="table-scroll"><table><thead><tr><th>Robot</th><th>Ordre des tâches</th></tr></thead><tbody><tr v-for="(queue, id) in data(result.robust.policy)" :key="id"><td>{{ id }}</td><td>{{ display(queue) }}</td></tr></tbody></table></div>
      <div v-if="scenarioRuns.length" class="table-scroll"><table data-testid="robust-scenarios"><thead><tr><th>Scénario déclaré</th><th>Indisponibilités</th><th>Résultat</th><th>Tâches terminées</th><th>Retard</th><th>Makespan</th></tr></thead><tbody><tr v-for="(run, index) in scenarioRuns" :key="index" :class="{ 'current-row': index === scenarioIndex }"><td>{{ index + 1 }}{{ index === scenarioIndex ? ' · rejoué' : '' }}</td><td>{{ display(run.calendar ?? scenarios[index]) }}</td><td>{{ display(run.execution_status) }}</td><td>{{ display(data(run.metrics).tasks_completed) }}</td><td>{{ display(data(run.metrics).total_tardiness) }}</td><td>{{ display(data(run.metrics).makespan) }}</td></tr></tbody></table></div>
      <p class="hint">Les métriques de scénario sont finales ; dates réalisées et opérations suivent uniquement scénario rejoué.</p>
    </details>

    <details v-if="trace" data-testid="schedule-execution-details"><summary>Opérations exécutées et stations jusqu’au tick {{ tick }}</summary>
      <div v-if="operations.length" class="table-scroll schedule-operations"><table data-testid="schedule-operations"><thead><tr><th>Tick</th><th>Robot / tâche</th><th>Opération réalisée</th><th>Station</th></tr></thead><tbody><tr v-for="(row, index) in operations" :key="index" :class="{ 'current-row': row.tick === tick }"><td>{{ row.tick }}</td><td>{{ row.robot }} / {{ row.task }}</td><td>{{ row.operation }}</td><td>{{ row.resources }}</td></tr></tbody></table></div>
      <p v-else class="hint">Aucune opération exécutée enregistrée à ce tick.</p>
      <div v-if="trace.instance.resources.length" class="table-scroll"><table data-testid="schedule-resources"><thead><tr><th>Station</th><th>Capacité</th><th>Occupants effectifs au tick {{ tick }}</th></tr></thead><tbody><tr v-for="resource in trace.instance.resources" :key="resource.id"><td>{{ resource.id }}</td><td>{{ resource.capacity }}</td><td>{{ snapshot?.resources[resource.id]?.holders.join(', ') || 'Libre' }}</td></tr></tbody></table></div>
      <div v-if="reservations.length" class="table-scroll"><table data-testid="schedule-reservations"><thead><tr><th>Station</th><th>Robot</th><th>Acquisition réalisée</th><th>Libération réalisée</th></tr></thead><tbody><tr v-for="(row, index) in reservations" :key="index"><td>{{ row.resource_id }}</td><td>{{ row.robot_id }}</td><td>{{ row.tick }}</td><td>{{ row.released === undefined ? 'Réservation conservée' : row.released }}</td></tr></tbody></table></div>
      <p class="hint">Présence sur case de station distincte de réservation. Libération après opérations du tick ; suspension peut conserver réservation.</p>
    </details>
    </details>
  </div>
</template>

<style scoped>
.schedule-chart-scroll { max-width: 100%; overflow: auto; margin-top: 14px; border: 1px solid #dbe4ec; border-radius: 7px; }
.schedule-chart { display: block; max-width: none; height: auto; font-size: 12px; color: #425b72; }
.schedule-chart text { font-family: inherit; }
.schedule-axis { cursor: pointer; }
.schedule-axis line { stroke: #dce5ed; }
.schedule-axis text { fill: #425b72; }
.schedule-task-label { font-size: 13px; font-weight: 700; }
.schedule-operation-label, .schedule-track-label { font-size: 10px; fill: #576e85; }
.schedule-missing { font-size: 12px; fill: #576e85; }
.schedule-milestone, .schedule-chart [role="button"] { cursor: pointer; }
.schedule-milestone text { font-size: 9px; font-weight: 700; pointer-events: none; }
.schedule-future { opacity: .75; }
.schedule-past-plan { opacity: .5; }
.schedule-current-event circle { stroke-width: 3.5px; }
.schedule-active-lane .schedule-lane-background { fill: #e9f3ff; fill-opacity: .6; }
.schedule-selected-lane .schedule-lane-background { stroke: #2162b8; stroke-width: 1px; }
.schedule-current-phase rect { opacity: .95; stroke: #172f47; stroke-width: 1px; }
.schedule-chart [role="button"]:focus-visible { outline: 2px solid #2162b8; outline-offset: 3px; }
.schedule-cursor text { font-size: 11px; fill: #172f47; font-weight: 700; }
.schedule-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 9px; font-size: 12px; }
.schedule-late-key { color: #a1263c; }
.schedule-plan-picker { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.schedule-plan-picker select { max-width: 100%; }
.schedule-inspection { display: flex; flex-direction: column; gap: 5px; padding: 12px; margin-top: 12px; background: #edf4fb; border-radius: 6px; font-size: 13px; overflow-wrap: anywhere; }
.schedule-technical { margin-top: 16px; }
.schedule-heading, .schedule-task-heading { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; }
.schedule-heading { justify-content: space-between; }
.schedule-heading h3 { margin: 0; }
.schedule-heading output { font-size: 13px; font-weight: 600; padding: 6px 9px; background: #e9f3ff; border-radius: 6px; }
.schedule-status { font-size: 13px; line-height: 1.5; margin-top: 8px; }
.schedule-current { display: flex; flex-wrap: wrap; gap: 5px 14px; padding: 10px 12px; margin-top: 12px; background: #e9f3ff; border-radius: 6px; font-size: 13px; }
.robot-calendars { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 0 20px; }
.robot-calendar { min-width: 0; }
.robot-calendar h4 { margin: 14px 0 8px; font-size: 14px; }
.schedule-tasks { margin: 0; padding-left: 24px; }
.schedule-task { padding: 10px 12px; margin-bottom: 8px; border: 1px solid #dbe4ec; border-radius: 7px; font-size: 13px; overflow-wrap: anywhere; }
.schedule-task::marker { font-weight: 600; color: #576e85; }
.schedule-task-heading { line-height: 1.5; }
.schedule-task-heading .badge { margin-left: 0; }
.schedule-dates { grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 8px 0 0; gap: 10px; font-size: 13px; }
.schedule-dates dt { margin-bottom: 4px; color: #425b72; font-weight: 600; }
.schedule-dates dd span { display: block; line-height: 1.5; }
.schedule-dates dd span:first-child { color: #576e85; }
.schedule-deadline { margin-top: 8px; font-size: 13px; line-height: 1.5; }
.schedule-operations { max-height: 300px; }
@media (max-width: 480px) {
  .schedule-task { padding: 10px 8px; }
  .schedule-dates { grid-template-columns: 1fr; gap: 7px; }
  .schedule-dates > div { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 8px; }
}
</style>
