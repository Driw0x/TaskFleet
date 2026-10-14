<script setup lang="ts">
import { computed, reactive } from 'vue'
import { robotLabels, taskLabels, type Instance, type Position, type Snapshot, type TaskState } from '../types'

const props = defineProps<{
  instance: Instance; snapshot: Snapshot; selected: string | null; selectedTask?: string | null
  selectedResource?: string | null; participants: Set<string>; animate: boolean; duration: number
}>()
const emit = defineEmits<{ select: [id: string | null]; selectTask: [id: string]; selectResource: [id: string] }>()
const cell = 64
const center = (p: Position) => `${p[0] * cell + cell / 2},${p[1] * cell + cell / 2}`
const endpoint = (p: Position, pickup: boolean) => `${p[0] * cell + cell / 2 + (pickup ? -15 : 15)},${p[1] * cell + cell / 2 + 22}`
const filters = reactive({ scheduled: false, pending: true, active: true, completed: false })
const stateColors: Record<TaskState, string> = { scheduled: '#64748b', pending: '#a86b0e', assigned: '#2563eb', picked_up: '#2563eb', completed: '#148263' }
const focusedTask = computed(() => props.selectedTask ?? (props.selected ? props.snapshot.robots[props.selected]?.task_id : null))
const task = computed(() => props.instance.tasks.find(t => t.id === focusedTask.value))
const relatedRobot = computed(() => props.selected ?? (props.selectedTask ? props.snapshot.tasks[props.selectedTask]?.robot_id : null))
const hasSelection = computed(() => !!(props.selected || props.selectedTask || props.selectedResource))
const selectedRobot = computed(() => props.selected ? props.snapshot.robots[props.selected] : null)
const resourceId = computed(() => selectedRobot.value?.resource_id ?? (selectedRobot.value?.state.startsWith('waiting_')
  ? Object.keys(props.snapshot.resources).find(id => String(props.snapshot.resources[id]!.position) === String(selectedRobot.value!.position)) : null))
const markers = computed(() => {
  const groups = new Map<string, { position: Position; pickup: boolean; tasks: string[]; states: TaskState[] }>()
  for (const task of props.instance.tasks) {
    const state = props.snapshot.tasks[task.id]?.state
    if (!state || !(task.id === focusedTask.value || filters[state === 'assigned' || state === 'picked_up' ? 'active' : state])) continue
    for (const pickup of [true, false]) {
      const position = pickup ? task.pickup : task.destination
      const key = `${position}:${pickup}`
      if (!groups.has(key)) groups.set(key, { position, pickup, tasks: [], states: [] })
      const group = groups.get(key)!
      group.tasks.push(task.id)
      group.states.push(state)
    }
  }
  return [...groups.entries()].map(([key, group]) => {
    const focused = group.tasks.includes(focusedTask.value ?? '')
    const states = focused ? [props.snapshot.tasks[focusedTask.value!]!.state] : [...new Set(group.states)]
    return { key, ...group, focused, states, id: focused ? focusedTask.value! : group.tasks[0]! }
  })
})
const robots = computed(() => {
  const entries = Object.entries(props.snapshot.robots)
  return entries.map(([id, robot]) => {
    const colocated = entries.filter(([, other]) => String(other.position) === String(robot.position))
    const index = colocated.findIndex(([otherId]) => otherId === id)
    const columns = Math.ceil(Math.sqrt(colocated.length))
    const rows = Math.ceil(colocated.length / columns)
    return { id, ...robot, x: robot.position[0] * cell + cell / 2 + (colocated.length > 1 ? (index % columns + .5) * 50 / columns - 25 : 0),
      y: robot.position[1] * cell + cell / 2 + (colocated.length > 1 ? (Math.floor(index / columns) + .5) * 24 / rows - 12 : 0),
      badgeWidth: Math.min(24, 50 / columns - 2), badgeHeight: Math.min(16, 24 / rows - 2), colocated: colocated.length }
  })
})
const path = computed(() => selectedRobot.value?.path.length
  ? [selectedRobot.value.position, ...selectedRobot.value.path].map(center).join(' ') : '')
function markerTitle(marker: { pickup: boolean; position: Position; tasks: string[] }) {
  const details = marker.tasks.map(id => {
    const state = props.snapshot.tasks[id]!
    return `${id}: ${taskLabels[state.state]}, robot ${state.robot_id ?? 'aucun'}`
  }).join(' ; ')
  return `${marker.pickup ? 'Collecte' : 'Destination'} [${marker.position}] — ${details}. Cliquer pour inspecter ; toutes les tâches restent accessibles dans le panneau.`
}
</script>

<template>
  <fieldset class="task-filters"><legend>Tâches sur la grille</legend>
    <label v-for="[key, label] in [['scheduled', 'Non arrivées'], ['pending', 'En attente'], ['active', 'Affectées / en cours'], ['completed', 'Terminées']] as const" :key="key">
      <input v-model="filters[key]" type="checkbox" :data-testid="`filter-${key}`">{{ label }}
    </label>
  </fieldset>
  <div class="warehouse-scroll"><svg class="warehouse" :viewBox="`-18 -18 ${instance.width * cell + 36} ${instance.height * cell + 36}`" role="group" aria-label="Entrepôt interactif">
    <title>Entrepôt {{ instance.width }} × {{ instance.height }} — tick {{ snapshot.tick }}</title>
    <defs>
      <pattern id="cells" :width="cell" :height="cell" patternUnits="userSpaceOnUse"><rect :width="cell" :height="cell" fill="#f5f8fb" stroke="#d7e1eb" stroke-width="1" /></pattern>
      <marker id="route-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M 0 0 L 10 5 L 0 10 Z" fill="#2679d3" /></marker>
      <marker id="relation-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 Z" fill="#845fb7" /></marker>
    </defs>
    <rect :width="instance.width * cell" :height="instance.height * cell" fill="url(#cells)" @click="emit('select', null)" />
    <text v-for="x in instance.width" :key="`x${x}`" :x="(x - 1) * cell + cell / 2" y="-6" class="axis">{{ x - 1 }}</text>
    <text v-for="y in instance.height" :key="`y${y}`" x="-9" :y="(y - 1) * cell + cell / 2 + 2" class="axis">{{ y - 1 }}</text>
    <rect v-for="p in instance.obstacles" :key="String(p)" :x="p[0] * cell + 2" :y="p[1] * cell + 2" :width="cell - 4" :height="cell - 4" rx="5" fill="#334155"><title>Obstacle [{{ p.join(',') }}]</title></rect>
    <g v-for="(resource, id) in snapshot.resources" :key="id" :data-resource="id" :data-position="String(resource.position)" :data-saturated="resource.holders.length >= resource.capacity"
       :transform="`translate(${center(resource.position)})`" class="station" :class="{ focused: selectedResource === id || resourceId === id, muted: hasSelection && selectedResource !== id && resourceId !== id }"
       role="button" tabindex="0" :aria-label="`Station ${id}, ${resource.holders.length} place(s) occupée(s) sur ${resource.capacity}`" :aria-pressed="selectedResource === id"
       @click="emit('selectResource', String(id))" @keydown.enter.prevent="emit('selectResource', String(id))" @keydown.space.prevent="emit('selectResource', String(id))">
      <rect x="-30" y="-30" width="60" height="60" rx="6" fill="#eee6ff" :stroke="resource.holders.length >= resource.capacity ? '#ac3850' : '#845fb7'" stroke-width="2" />
      <rect x="-29" y="-29" width="58" height="17" rx="3" fill="#eee6ff" />
      <text x="-28" y="-19" class="station-label" :textLength="String(id).length > 8 ? 39 : undefined" lengthAdjust="spacingAndGlyphs">{{ id }}</text>
      <text x="29" y="-19" class="station-count">{{ resource.holders.length }}/{{ resource.capacity }}</text>
      <title>{{ id }} [{{ resource.position.join(',') }}] — {{ resource.holders.length }} place(s) occupée(s) sur {{ resource.capacity }} ; {{ resource.holders.length >= resource.capacity ? 'saturée' : 'disponible' }} ; robots : {{ resource.holders.join(', ') || 'aucun' }}</title>
    </g>
    <polyline v-if="task" data-testid="task-relation" :points="`${endpoint(task.pickup, true)} ${endpoint(task.destination, false)}`" class="task-relation" marker-end="url(#relation-arrow)"><title>Relation logique {{ task.id }} : collecte vers destination, sans indication de trajet</title></polyline>
    <polyline v-if="path" data-testid="robot-path" :points="path" class="robot-path" marker-end="url(#route-arrow)"><title>Chemin restant fourni par le snapshot</title></polyline>
    <g v-for="marker in markers" :key="marker.key" :data-tasks="marker.tasks.join(',')" :data-endpoint="marker.pickup ? 'pickup' : 'delivery'" :data-position="String(marker.position)"
       :transform="`translate(${center(marker.position)})`" class="task-marker" :class="{ focused: marker.focused, muted: hasSelection && !marker.focused }"
       role="button" tabindex="0" :aria-label="`${marker.pickup ? 'Collecte' : 'Destination'} : ${marker.tasks.join(', ')}`" :aria-pressed="marker.focused"
       @click="emit('selectTask', marker.id)" @keydown.enter.prevent="emit('selectTask', marker.id)" @keydown.space.prevent="emit('selectTask', marker.id)">
      <g :opacity="marker.states.every(s => s === 'completed') ? 0.45 : marker.states.every(s => s === 'scheduled') ? 0.55 : 1">
        <rect :x="marker.pickup ? -29 : 2" y="16" width="27" height="13" rx="3" :fill="marker.pickup ? '#fff3d9' : '#ddf5e9'" :stroke="marker.pickup ? '#a86b0e' : '#148263'" />
        <rect v-if="marker.pickup" x="-27" y="20" width="5" height="5" fill="#a86b0e" />
        <path v-else d="M 7 19 L 10 22 L 7 25 L 4 22 Z" fill="#148263" />
        <text :x="marker.pickup ? -12 : 19" y="25" class="marker-label" :textLength="(marker.focused ? marker.id : marker.tasks.length > 1 ? '' : marker.id).length > 3 ? 16 : undefined" lengthAdjust="spacingAndGlyphs">{{ marker.focused || marker.tasks.length === 1 ? marker.id : `×${marker.tasks.length}` }}</text>
        <line v-for="(state, i) in marker.states" :key="state" :data-task-state="state" :x1="(marker.pickup ? -28 : 3) + i * 25 / marker.states.length" :x2="(marker.pickup ? -28 : 3) + (i + 1) * 25 / marker.states.length" y1="29" y2="29" :stroke="stateColors[state]" stroke-width="3" :stroke-dasharray="state === 'scheduled' ? '2 2' : undefined" />
      </g>
      <title>{{ markerTitle(marker) }}</title>
    </g>
    <g v-for="robot in robots" :key="robot.id" :data-robot="robot.id" :data-position="String(robot.position)" :transform="`translate(${robot.x},${robot.y})`"
       :style="{ transition: animate ? `transform ${duration}ms linear` : 'none' }"
       class="robot" :class="{ muted: hasSelection && relatedRobot !== robot.id && !snapshot.resources[selectedResource ?? '']?.holders.includes(robot.id) }"
       role="button" tabindex="0" :aria-label="`Sélectionner ${robot.id}`" :aria-pressed="selected === robot.id"
       @click="emit('select', selected === robot.id ? null : robot.id)" @keydown.enter.prevent="emit('select', selected === robot.id ? null : robot.id)" @keydown.space.prevent="emit('select', selected === robot.id ? null : robot.id)">
      <template v-if="robot.colocated > 1">
        <rect :x="-robot.badgeWidth / 2 - 2" :y="-robot.badgeHeight / 2 - 2" :width="robot.badgeWidth + 4" :height="robot.badgeHeight + 4" rx="3" fill="none" :stroke="relatedRobot === robot.id ? '#2563eb' : participants.has(robot.id) ? '#a86b0e' : 'none'" :stroke-dasharray="relatedRobot !== robot.id ? '3 3' : undefined" />
        <rect :x="-robot.badgeWidth / 2" :y="-robot.badgeHeight / 2" :width="robot.badgeWidth" :height="robot.badgeHeight" rx="2" :fill="robot.unavailable ? '#ca4050' : '#167581'" />
      </template>
      <template v-else>
        <circle v-if="participants.has(robot.id)" r="17" fill="none" stroke="#a86b0e" stroke-width="2" stroke-dasharray="3 3" />
        <circle v-if="relatedRobot === robot.id" r="15" fill="none" stroke="#2563eb" stroke-width="2" />
        <circle r="12" :fill="robot.unavailable ? '#ca4050' : '#167581'" stroke="white" stroke-width="1.5" />
      </template>
      <text y="3" class="robot-label" :style="{ fontSize: robot.colocated > 1 ? `${Math.min(9, robot.badgeHeight - 2)}px` : '10px' }" :textLength="robot.id.length > 3 ? robot.badgeWidth - 3 : undefined" lengthAdjust="spacingAndGlyphs">{{ robot.id }}</text>
      <title>{{ robot.id }} — case [{{ robot.position.join(',') }}], {{ robot.unavailable ? 'suspendu' : robotLabels[robot.state] ?? robot.state }}, tâche {{ robot.task_id || 'aucune' }}{{ robot.colocated > 1 ? ` ; ${robot.colocated} robots dans cette case` : '' }}</title>
    </g>
  </svg></div>
  <div class="legend" aria-label="Légende de l’entrepôt">
    <span><i class="legend-robot" />Robot</span><span><i class="legend-robot selected-symbol" />Robot sélectionné / affecté</span><span><i class="legend-robot shared-symbol" />Robots dans une même case</span>
    <span><i class="legend-cell obstacle-symbol" />Obstacle</span><span><i class="legend-cell" />Case libre</span>
    <span class="orange">■ Collecte</span><span class="green">◆ Destination de livraison</span><span class="purple">▣ Station / ressource partagée</span><span class="red">▣ Station saturée</span>
    <span><i class="state-swatch pending-symbol" />En attente</span><span><i class="state-swatch active-symbol" />Affectée / en cours</span>
    <span><i class="state-swatch completed-symbol" />Terminée (opacité 45 %)</span><span><i class="state-swatch scheduled-symbol" />Non arrivée (opacité 55 %)</span>
    <span class="red">● Robot suspendu</span><span class="orange">◌ Échange CNP</span>
    <span class="route-key">– – Chemin restant prévu</span><span class="purple">··· Relation collecte / livraison</span>
  </div>
  <p class="hint">ID ou ×nombre de tâches par extrémité ; traits d’état sous les badges. Survol : détails. Sélection : inspection et tâche toujours visible malgré les filtres. Éléments sans rapport atténués. Stations : places occupées / capacité. Les collisions physiques ne sont pas simulées.</p>
  <p class="hint mobile-grid-hint">Faire défiler la grille horizontalement pour voir toutes les cases.</p>
</template>
