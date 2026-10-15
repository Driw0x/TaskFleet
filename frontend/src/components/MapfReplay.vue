<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MapfProblem, ScientificData } from '../experiments'
import { data, list } from '../experiments'
import type { Position } from '../types'

const props = defineProps<{ problem: MapfProblem; plan: ScientificData; tick: number }>()
const colors = ['#126e7d', '#845fb7', '#ad611b', '#2162b8', '#a82a3f', '#148263']
const center = (p: Position) => `${p[0] * 56 + 28},${p[1] * 56 + 28}`
const same = (a: Position | undefined, b: Position) => !!a && a[0] === b[0] && a[1] === b[1]
const selected = ref<string | null>(null)
watch(() => props.plan, () => { selected.value = null })
const paths = computed(() => data(props.plan.paths) as Record<string, Position[]>)
const hasPlan = computed(() => props.plan.paths !== null && typeof props.plan.paths === 'object' && !Array.isArray(props.plan.paths))
const agents = computed(() => props.problem.agents.map((agent, index) => {
  const path = paths.value[agent.id] ?? []
  const position = path[Math.min(props.tick, path.length - 1)] ?? agent.start
  const previous = path[Math.min(Math.max(0, props.tick - 1), path.length - 1)]
  const arrival = path.findIndex(cell => same(cell, agent.goal))
  const arrived = arrival >= 0 && props.tick >= arrival
  const waiting = !!path.length && !arrived && props.tick > 0 && same(previous, position)
  const distance = path.slice(1, arrival + 1).filter((cell, step) => !same(path[step], cell)).length
  return { ...agent, color: colors[index % colors.length], path, position, waiting, arrived,
    arrival, distance, waits: arrival < 0 ? null : arrival - distance,
    state: !path.length ? 'Départ · aucun plan' : arrived ? 'Destination atteinte · occupation persistante'
      : props.tick === 0 ? 'Départ' : waiting ? 'Attente' : 'Déplacement' }
}))
const inspected = computed(() => agents.value.find(agent => agent.id === selected.value))
const conflicts = computed(() => list(data(props.plan.validation).violations).map(data)
  .filter(conflict => conflict.tick === props.tick))
</script>

<template>
  <p class="hint">MAPF isolé : collisions de sommet et échanges d’arêtes interdits. Buts absorbants et persistants. Collecte et livraison hors de ce modèle.</p>
  <div class="warehouse-scroll"><svg class="warehouse mapf" :viewBox="`-18 -18 ${problem.width * 56 + 36} ${problem.height * 56 + 36}`"
    :style="{ maxWidth: `${(problem.width * 56 + 36) * 1.25}px`, minWidth: `${problem.width * 56 + 36}px` }" role="img" aria-label="Trajectoires MAPF">
    <title>MAPF {{ problem.width }} × {{ problem.height }} — tick {{ tick }}</title>
    <defs><pattern id="mapf-cells" width="56" height="56" patternUnits="userSpaceOnUse"><rect width="56" height="56" fill="#f5f8fb" stroke="#d7e1eb" /></pattern></defs>
    <rect :width="problem.width * 56" :height="problem.height * 56" fill="url(#mapf-cells)" />
    <rect v-for="p in problem.obstacles" :key="String(p)" :x="p[0] * 56 + 2" :y="p[1] * 56 + 2" width="52" height="52" rx="5" fill="#334155" />
    <g data-layer="paths"><polyline v-for="agent in agents.filter(agent => agent.path.length)" :key="agent.id" :points="agent.path.map(center).join(' ')" :stroke="agent.color" stroke-width="3" fill="none" opacity=".5" /></g>
    <g data-layer="endpoints"><g v-for="agent in agents" :key="agent.id">
      <g :transform="`translate(${center(agent.start)})`"><rect x="-21" y="-21" width="42" height="42" rx="5" fill="none" :stroke="agent.color" stroke-dasharray="4 3" /><title>Départ {{ agent.id }} [{{ agent.start.join(', ') }}]</title></g>
      <g :transform="`translate(${center(agent.goal)})`"><path d="M -18 -18 L 18 18 M -18 18 L 18 -18" :stroke="agent.color" stroke-width="3" /><title>Destination {{ agent.id }} [{{ agent.goal.join(', ') }}]</title></g>
    </g></g>
    <g data-layer="robots"><g v-for="agent in agents" :key="agent.id" :data-mapf-agent="agent.id">
      <g role="button" tabindex="0" :aria-label="`Inspecter agent ${agent.id}`" :aria-pressed="selected === agent.id" class="robot" @click="selected = agent.id" @keydown.enter.prevent="selected = agent.id" @keydown.space.prevent="selected = agent.id">
      <g :transform="`translate(${center(agent.position)})`" :data-position="String(agent.position)" :data-waiting="!!agent.waiting">
        <circle v-if="selected === agent.id" r="23" fill="none" stroke="#2563eb" stroke-width="2" />
        <circle r="15" :fill="agent.color" :stroke="agent.arrived ? '#e8ac42' : 'white'" stroke-width="3" /><text class="robot-label" y="3">{{ agent.id }}</text>
        <text v-if="agent.waiting && !agent.arrived" y="-19" class="marker-label">Attente</text>
        <title>{{ agent.id }} [{{ agent.position.join(', ') }}] · {{ agent.state }}</title>
      </g>
      </g>
    </g></g>
    <circle v-for="(conflict, index) in conflicts.filter(conflict => Array.isArray(conflict.position))" :key="index" :cx="Number((conflict.position as number[])[0]) * 56 + 28" :cy="Number((conflict.position as number[])[1]) * 56 + 28" r="24" fill="none" stroke="#c43b50" stroke-width="5" />
  </svg></div>
  <div class="legend"><span>□ pointillé : départ</span><span>× : destination</span><span>Trait : trajectoire complète</span><span>Cercle doré : but occupé</span></div>
  <div class="table-scroll"><table data-testid="mapf-agents"><thead><tr><th>Agent</th><th>Départ</th><th>Destination</th><th>Position au tick {{ tick }}</th><th>État</th><th>Première arrivée</th></tr></thead><tbody><tr v-for="agent in agents" :key="agent.id" :class="{ 'current-row': selected === agent.id }"><td><button class="inline-button" :style="{ color: agent.color }" @click="selected = agent.id">{{ agent.id }}</button></td><td>[{{ agent.start.join(', ') }}]</td><td>[{{ agent.goal.join(', ') }}]</td><td>[{{ agent.position.join(', ') }}]</td><td>{{ agent.state }}</td><td>{{ agent.arrival < 0 ? '—' : agent.arrival }}</td></tr></tbody></table></div>
  <p v-if="inspected" class="hint" data-testid="mapf-inspection">Agent {{ inspected.id }} · Déplacements : {{ inspected.path.length ? inspected.distance : '—' }} · Attentes avant arrivée : {{ inspected.waits ?? '—' }}. Mesures du plan complet ; occupation persistante du but exclue des attentes.</p>
  <p v-if="conflicts.length" class="error" role="alert">Conflits au tick {{ tick }} : {{ conflicts }}</p>
  <p v-if="!hasPlan" class="notice">Aucun plan vérifié disponible. La grille montre uniquement les départs et destinations.</p>
</template>

<style scoped>
.warehouse-scroll { max-height: 520px; overflow: auto; }
.warehouse { height: auto; min-height: 0; max-height: none; margin-inline: auto; }
.legend { font-size: 12px; }
</style>
