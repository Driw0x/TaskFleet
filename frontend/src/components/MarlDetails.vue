<script setup lang="ts">
import { computed } from 'vue'
import { data, display, list } from '../experiments'
import type { ScientificData } from '../experiments'
import type { Result } from '../types'

const props = defineProps<{ episode: ScientificData; tick: number; trace?: Result['trace'] }>()
const emit = defineEmits<{ seek: [tick: number] }>()
const decisions = computed(() => list(props.episode.decisions).map(data))
const rewards = computed(() => list(props.episode.rewards).map(value => typeof value === 'number' && Number.isFinite(value) ? value : null))
const decision = computed(() => decisions.value.find(row => Number(row.tick) === Math.max(0, props.tick - 1)))
const observations = computed(() => data(decision.value?.observations))
const llmTrace = computed(() => list(props.episode.llm_trace).map(data))
const snapshot = computed(() => props.trace?.current.tick === props.tick ? props.trace.current : props.trace?.snapshots.find(row => row.tick === props.tick))
const deliveries = computed(() => props.trace?.events.filter(event => event.kind === 'delivered' && event.tick === props.tick).map(event => event.task_id) ?? [])
const delivered = computed(() => props.trace?.events.filter(event => event.kind === 'delivered' && event.tick <= props.tick).length)
const returns = computed(() => {
  let total: number | null = 0
  return rewards.value.map(reward => total = total === null || reward === null ? null : total + reward)
})
const cumulative = computed(() => props.tick === 0 ? 0 : returns.value[props.tick - 1])
const policyNames: Record<string, string> = { random: 'Aléatoire légale', heuristic: 'Heuristique locale', q: 'Q-learning figée', llm: 'LLM Ollama' }
const policyName = computed(() => policyNames[String(props.episode.policy)] ?? display(props.episode.policy))
const deliveryEvents = computed(() => (props.trace?.events ?? []).filter(event => event.kind === 'delivered').slice().sort((a, b) => a.tick - b.tick))
const lastTick = computed(() => Math.max(1, props.trace?.current.tick ?? 0, rewards.value.length, ...decisions.value.map(row => Number(row.tick) + 1)))
const width = computed(() => Math.max(660, 130 + lastTick.value * 18))
const x = (value: number) => 88 + value / lastTick.value * (width.value - 120)
const marks = computed(() => {
  const step = Math.max(1, Math.ceil(lastTick.value / 10))
  return Array.from(new Set([...Array.from({ length: Math.floor(lastTick.value / step) + 1 }, (_, i) => i * step), lastTick.value]))
})
const series = computed(() => [
  { id: 'reward', label: 'Récompense par tick', color: '#2162b8', values: rewards.value.map((value, i) => ({ tick: i + 1, value })) },
  { id: 'return', label: 'Retour cumulé', color: '#845fb7', values: [{ tick: 0, value: 0 }, ...returns.value.map((value, i) => ({ tick: i + 1, value }))] },
  { id: 'completed', label: 'Tâches terminées', color: '#148263', values: props.trace ? Array.from({ length: props.trace.current.tick + 1 }, (_, tick) => ({ tick, value: deliveryEvents.value.filter(event => event.tick <= tick).length })) : [] },
].map((row, index) => {
  const recorded = row.values.flatMap(point => point.value === null ? [] : [point.value])
  const min = Math.min(0, ...recorded), max = Math.max(0, ...recorded)
  const top = 42 + index * 140
  const y = (value: number) => top + 76 - (value - min) / (max - min || 1) * 76
  let path = ''
  let previous = false
  for (const point of row.values) {
    if (point.value === null) { previous = false; continue }
    const position = `${x(point.tick)},${y(point.value)}`
    path += row.id === 'completed' && previous ? ` H ${x(point.tick)} V ${y(point.value)}` : ` ${previous ? 'L' : 'M'} ${position}`
    previous = true
  }
  return { ...row, top, min, max, y, path }
}))
const robots = computed(() => Array.from(new Set(decisions.value.flatMap(row => Object.keys(data(row.observations))).concat(deliveryEvents.value.flatMap(event => event.robot_id ? [event.robot_id] : [])))).sort())
const timeline = computed(() => decisions.value.flatMap(row => Object.keys(data(row.observations)).map(robot => {
  const task = data(row.proposals)[robot]
  const conflict = list(row.conflicts).includes(robot)
  const rejected = list(row.rejected).includes(robot)
  const accepted = list(row.accepted).some(pair => list(pair)[0] === robot && list(pair)[1] === task)
  const llm = llmTrace.value.find(entry => data(entry.observation).tick === row.tick && data(data(entry.observation).robot).id === robot)
  return { tick: Number(row.tick), robot, task, status: rejected ? 'rejected' : conflict ? 'conflict' : accepted ? 'accepted' : 'wait',
    label: rejected ? 'Proposition rejetée (hors masque)' : conflict ? 'Conflit : proposition non affectée' : accepted ? 'Proposition acceptée' : 'Attente légale',
    source: llm ? llm.fallback ? 'Secours' : 'LLM' : props.episode.policy === 'llm' ? 'Sans appel LLM' : policyName.value,
    fallback: !!llm?.fallback, llm: !!llm && !llm.fallback }
})).sort((a, b) => a.tick - b.tick || a.robot.localeCompare(b.robot)))
const timelineRowHeight = 88
const actionY = (robot: string) => 58 + robots.value.indexOf(robot) * timelineRowHeight
const deliveryY = (robot: string) => actionY(robot) + 28
const labelOnLeft = (tick: number) => x(tick) > width.value - 150
const labelX = (tick: number) => x(tick) + (labelOnLeft(tick) ? -9 : 9)
const statusColors: Record<string, string> = { accepted: '#148263', rejected: '#a82a3f', conflict: '#ad611b', wait: '#64748b' }
function action(value: unknown) { return value === null ? 'Attente' : display(value) }
function source(id: string) {
  return llmTrace.value.find(row => data(row.observation).tick === decision.value?.tick && data(data(row.observation).robot).id === id)
}
function applied(id: string) {
  const accepted = list(decision.value?.accepted).find(pair => list(pair)[0] === id)
  return accepted ? action(list(accepted)[1]) : 'Aucune nouvelle affectation'
}
function validation(id: string) {
  return list(decision.value?.rejected).includes(id) ? 'Action rejetée (hors masque)'
    : list(decision.value?.conflicts).includes(id) ? 'Conflit : priorité à un autre robot'
    : 'Action légale'
}
function provenance(id: string) {
  const row = source(id)
  if (row) return row.fallback ? `Secours : ${display(row.error)}` : 'Décision LLM validée'
  return props.episode.policy === 'llm' ? 'Aucun appel LLM pour cet agent' : `Politique ${policyName.value}`
}
</script>

<template>
  <div data-testid="marl-details">
    <h3>Épisode d’évaluation figée</h3>
    <p>Politique : {{ policyName }} · Résultat final : terminaison {{ episode.terminated ? 'Oui' : 'Non' }}, troncature {{ episode.truncated ? 'Oui' : 'Non' }}.</p>
    <p class="hint">Observations autorisées : robot propre, grille, tâches publiques arrivées, coûts propres et masque légal. Aucun autre robot, calendrier futur ou tâche future. Récompense commune : opposé des tâches arrivées encore inachevées après transition.</p>
    <p data-testid="marl-current">État au tick {{ tick }} : {{ snapshot ? snapshot.all_tasks_completed ? 'Terminé' : snapshot.finished ? 'Tronqué à l’horizon' : 'En cours' : 'État indisponible' }} · Récompense {{ tick === 0 ? '— (aucune transition)' : display(rewards[tick - 1]) }} · Retour cumulé {{ display(cumulative) }} · Livraisons ce tick : {{ trace ? deliveries.length ? deliveries.join(', ') : 'Aucune' : 'Indisponibles' }} · Total livré : {{ display(delivered) }}.</p>
    <div class="chart-scroll">
      <svg data-testid="marl-curves" :viewBox="`0 0 ${width} 450`" :style="{ width: `${width}px` }" role="img" aria-label="Récompenses et tâches terminées par tick de simulation">
        <title>Évaluation figée, tick {{ tick }}. Récompenses enregistrées après chaque transition ; livraisons issues des événements moteur.</title>
        <g v-for="row in series" :key="row.id" :data-series="row.id">
          <text x="10" :y="row.top - 17" class="chart-heading">{{ row.label }}</text>
          <line :x1="x(0)" :x2="x(lastTick)" :y1="row.top + 76" :y2="row.top + 76" class="axis-line" />
          <text :x="x(0) - 9" :y="row.top + 80" text-anchor="end">{{ row.min }}</text>
          <text v-if="row.max !== row.min" :x="x(0) - 9" :y="row.top + 4" text-anchor="end">{{ row.max }}</text>
          <g v-for="mark in marks" :key="mark"><line :x1="x(mark)" :x2="x(mark)" :y1="row.top" :y2="row.top + 76" class="grid-line" /><text :x="x(mark)" :y="row.top + 94" text-anchor="middle">{{ mark }}</text></g>
          <path :d="row.path" fill="none" :stroke="row.color" stroke-width="2" />
          <template v-for="point in row.values" :key="point.tick"><circle v-if="point.value !== null" :cx="x(point.tick)" :cy="row.y(point.value)" :r="tick === point.tick ? 5 : 3" :fill="row.color" class="chart-point" role="button" tabindex="0" :aria-label="`${row.label}, tick ${point.tick} : ${point.value}. Rejouer ce tick`" :data-tick="point.tick" :data-value="point.value" @click="emit('seek', point.tick)" @keydown.enter.prevent="emit('seek', point.tick)" @keydown.space.prevent="emit('seek', point.tick)"><title>Tick {{ point.tick }} · {{ row.label }} : {{ point.value }}</title></circle></template>
          <text v-if="!row.values.length" :x="x(0) + 12" :y="row.top + 40">{{ row.id === 'completed' ? 'Trace moteur indisponible' : 'Aucune valeur enregistrée' }}</text>
        </g>
        <line data-testid="marl-curve-cursor" :data-tick="tick" :x1="x(tick)" :x2="x(tick)" y1="25" y2="418" class="tick-cursor" />
        <text :x="x(tick)" y="13" text-anchor="middle" class="cursor-label">Tick {{ tick }}</text>
        <text :x="x(lastTick)" y="448" text-anchor="end">Ticks de simulation</text>
      </svg>
    </div>
    <p class="hint">Même axe temporel pour les trois courbes et la chronologie. Cliquer un point pour synchroniser la grille. Une valeur manquante interrompt la courbe ; le retour cumulé devient indisponible.</p>
    <h3>Chronologie des décisions et livraisons</h3>
    <div v-if="robots.length" class="chart-scroll">
      <svg data-testid="marl-timeline" :viewBox="`0 0 ${width} ${robots.length * timelineRowHeight + 66}`" :style="{ width: `${width}px` }" role="img" aria-label="Chronologie des propositions des robots et des tâches terminées">
        <g v-for="mark in marks" :key="mark"><line :x1="x(mark)" :x2="x(mark)" y1="32" :y2="robots.length * timelineRowHeight + 24" class="grid-line" /><text :x="x(mark)" y="19" text-anchor="middle">{{ mark }}</text></g>
        <g v-for="robot in robots" :key="robot"><text x="10" :y="actionY(robot) + 4">{{ robot }}</text><line :x1="x(0)" :x2="x(lastTick)" :y1="actionY(robot)" :y2="actionY(robot)" class="axis-line" /><line :x1="x(0)" :x2="x(lastTick)" :y1="deliveryY(robot)" :y2="deliveryY(robot)" class="delivery-line" /></g>
        <g v-for="(event, index) in timeline" :key="index" data-action-event :data-tick="event.tick" :data-robot="event.robot" :data-status="event.status" :data-source="event.source" :class="{ 'future-event': event.tick >= tick, 'selected-event': event.tick === Number(decision?.tick) }" class="action-event" role="button" tabindex="0" :aria-label="`${event.robot}, tick ${event.tick}, ${action(event.task)} : ${event.label}, ${event.source}. Inspecter la transition vers ${event.tick + 1}`" @click="emit('seek', event.tick + 1)" @keydown.enter.prevent="emit('seek', event.tick + 1)" @keydown.space.prevent="emit('seek', event.tick + 1)">
          <circle :cx="x(event.tick)" :cy="actionY(event.robot)" :r="event.status === 'wait' ? 2 : 7" :fill="statusColors[event.status]" :stroke="event.fallback ? '#845fb7' : event.llm ? '#2162b8' : 'white'" :stroke-width="event.fallback || event.llm ? 3 : 1" :stroke-dasharray="event.fallback ? '2 2' : undefined" />
          <text v-if="event.status !== 'wait'" :x="labelX(event.tick)" :y="actionY(event.robot) - 11" :text-anchor="labelOnLeft(event.tick) ? 'end' : 'start'">{{ action(event.task) }}</text>
          <text v-if="event.status !== 'wait'" :x="labelX(event.tick)" :y="actionY(event.robot) + 23" :text-anchor="labelOnLeft(event.tick) ? 'end' : 'start'" class="source-label">{{ event.source }}</text>
          <title>Tick {{ event.tick }} · {{ event.robot }} propose {{ action(event.task) }} · {{ event.label }} · {{ event.source }}. Observation avant la transition {{ event.tick }} vers {{ event.tick + 1 }}.</title>
        </g>
        <g v-for="(event, index) in deliveryEvents" :key="`delivery-${index}`" data-delivery-event :data-tick="event.tick" :class="{ 'future-event': event.tick > tick }" class="action-event" role="button" tabindex="0" :aria-label="`${event.task_id} terminée par ${event.robot_id} au tick ${event.tick}`" @click="emit('seek', event.tick)" @keydown.enter.prevent="emit('seek', event.tick)" @keydown.space.prevent="emit('seek', event.tick)">
          <path :d="`M ${x(event.tick)} ${deliveryY(event.robot_id ?? '') - 7} l 7 7 l -7 7 l -7 -7 Z`" fill="#148263" stroke="white" />
          <text :x="labelX(event.tick)" :y="deliveryY(event.robot_id ?? '') + 13" :text-anchor="labelOnLeft(event.tick) ? 'end' : 'start'">{{ event.task_id }} ✓</text><title>Livraison exécutée : {{ event.task_id }}, robot {{ event.robot_id }}, tick {{ event.tick }}</title>
        </g>
        <line data-testid="marl-timeline-cursor" :data-tick="tick" :x1="x(tick)" :x2="x(tick)" y1="28" :y2="robots.length * timelineRowHeight + 24" class="tick-cursor" />
        <text :x="x(lastTick)" :y="robots.length * timelineRowHeight + 56" text-anchor="end">Ticks de simulation</text>
      </svg>
    </div>
    <div class="legend"><span class="green">● Acceptée</span><span class="red">● Rejetée</span><span class="orange">● Conflit non affecté</span><span>· Attente</span><span class="green">◆ Terminée</span><span>Contour bleu : LLM</span><span>Contour violet pointillé : secours</span><span>Atténué : suite enregistrée du rejeu</span></div>
    <p class="hint">Pour chaque robot : propositions sur la ligne haute, livraisons sur la ligne basse. Proposition et acceptation au tick indiqué, avant transition. Cliquer une proposition affiche la transition aboutissant au tick suivant. Les livraisons correspondent aux événements exécutés, y compris ceux encore à rejouer.</p>
    <details data-testid="marl-technical-details"><summary>Détails de l’épisode : récompenses, observations locales et actions</summary>
    <div v-if="rewards.length" class="table-scroll reward-table"><table data-testid="rewards"><thead><tr><th>Tick après transition</th><th>Récompense commune</th><th>Retour cumulé</th></tr></thead><tbody>
      <tr v-for="(reward, index) in rewards" :key="index" :class="{ 'current-row': tick === index + 1 }"><td><button @click="emit('seek', index + 1)">{{ index + 1 }}</button></td><td>{{ display(reward) }}</td><td>{{ display(returns[index]) }}</td></tr>
    </tbody></table></div>
    <label v-if="decisions.length">Inspecter la transition (synchronise le rejeu)<select :value="tick === 0 ? '' : tick - 1" data-testid="decision-tick" @change="emit('seek', ($event.target as HTMLSelectElement).value === '' ? 0 : Number(($event.target as HTMLSelectElement).value) + 1)"><option value="">État initial (tick 0)</option><option v-for="row in decisions" :key="String(row.tick)" :value="Number(row.tick)">{{ row.tick }} vers {{ Number(row.tick) + 1 }}</option></select></label>
    <template v-if="decision">
      <p data-testid="marl-transition">{{ tick === 0 ? 'Prochaine transition, pas encore rejouée' : 'Transition aboutissant à l’état affiché' }} : {{ decision.tick }} vers {{ Number(decision.tick) + 1 }}. Observations et propositions au tick {{ decision.tick }}, avant transition. La grille et les livraisons montrent l’état global du moteur ; les agents reçoivent uniquement les observations locales ci-dessous.</p>
      <div class="table-scroll"><table data-testid="marl-actions"><thead><tr><th>Agent</th><th>Observation locale / masque</th><th>Action proposée</th><th>Action appliquée à la transition</th><th>Validation et provenance</th></tr></thead><tbody>
        <tr v-for="(observation, id) in observations" :key="id"><th>{{ id }}</th><td>{{ display(data(observation).legal_actions) }}<details><summary>Observation réellement fournie</summary><pre>{{ JSON.stringify(observation, null, 2) }}</pre></details></td><td>{{ action(data(decision.proposals)[id]) }}</td><td>{{ applied(String(id)) }}</td><td>{{ validation(String(id)) }} · {{ provenance(String(id)) }}<details v-if="source(String(id))"><summary>Réponse et validation Ollama</summary><pre>{{ JSON.stringify(source(String(id)), null, 2) }}</pre></details></td></tr>
      </tbody></table></div>
      <details><summary>Observations locales, propositions, affectations et validations originales</summary><pre data-testid="episode-decision">{{ JSON.stringify(decision, null, 2) }}</pre></details>
    </template>
    <p v-else class="empty">Aucune décision enregistrée pour cette transition.</p>
    </details>
    <p v-if="!rewards.length" class="empty">Aucune récompense enregistrée.</p>
    <p v-if="!decisions.length" class="empty">Aucune décision enregistrée : chronologie des propositions indisponible.</p>
    <p v-if="!trace" class="empty">Trace moteur indisponible : courbe des tâches terminées et livraisons indisponibles.</p>
  </div>
</template>

<style scoped>
.chart-scroll { max-width: 100%; overflow-x: auto; margin-top: 16px; }
svg { display: block; max-width: none; font-size: 11px; fill: #344c63; }
.chart-heading { font-size: 13px; font-weight: 600; }
.axis-line { stroke: #a8bacb; }
.delivery-line { stroke: #d5dfe9; stroke-dasharray: 3 4; }
.grid-line { stroke: #e3eaf1; }
.tick-cursor { stroke: #a82a3f; stroke-width: 1.5; stroke-dasharray: 5 3; pointer-events: none; }
.cursor-label { fill: #a82a3f; font-weight: 600; }
.chart-point, .action-event { cursor: pointer; }
.future-event { opacity: .4; }
.selected-event circle { stroke-width: 3; }
.source-label { font-size: 9px; }
</style>
