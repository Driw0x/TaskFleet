<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { Instance } from '../types'

const props = defineProps<{ instance: Instance; busy: boolean; valid?: boolean; jsonDraft: string }>()
const emit = defineEmits<{
  change: [instance: Instance]; generate: [parameters: Record<string, unknown>]
  import: [content: string]; draft: [content: string]; export: []
}>()
type Collection = 'robots' | 'tasks' | 'obstacles' | 'resources' | 'unavailability'
type Column = { label: string; path: (string | number)[]; text?: boolean; nullable?: boolean }
const coordinate = (label: string, name: string, axis: number): Column => ({ label, path: [name, axis] })
const tables: { key: Collection; label: string; columns: Column[]; row: unknown }[] = [
  { key: 'robots', label: 'Robots', columns: [{ label: 'ID', path: ['id'], text: true }, coordinate('x', 'position', 0), coordinate('y', 'position', 1), { label: 'Capacité', path: ['capacity'] }], row: { id: '', position: [0, 0], capacity: 1 } },
  { key: 'tasks', label: 'Tâches', columns: [{ label: 'ID', path: ['id'], text: true }, coordinate('Collecte x', 'pickup', 0), coordinate('Collecte y', 'pickup', 1), coordinate('Destination x', 'destination', 0), coordinate('Destination y', 'destination', 1), { label: 'Charge', path: ['load'] }, { label: 'Manutention', path: ['handling_ticks'] }, { label: 'Échéance', path: ['deadline'], nullable: true }, { label: 'Arrivée', path: ['arrival_tick'] }], row: { id: '', pickup: [0, 0], destination: [0, 0], load: 1, handling_ticks: 1, deadline: null, arrival_tick: 0 } },
  { key: 'obstacles', label: 'Obstacles', columns: [{ label: 'x', path: [0] }, { label: 'y', path: [1] }], row: [0, 0] },
  { key: 'resources', label: 'Stations / ressources', columns: [{ label: 'ID', path: ['id'], text: true }, coordinate('x', 'position', 0), coordinate('y', 'position', 1), { label: 'Capacité', path: ['capacity'] }], row: { id: '', position: [0, 0], capacity: 1 } },
  { key: 'unavailability', label: 'Indisponibilités', columns: [{ label: 'Robot', path: ['robot_id'], text: true }, { label: 'Début', path: ['start_tick'] }, { label: 'Fin exclue', path: ['end_tick'] }], row: { robot_id: '', start_tick: 0, end_tick: 1 } },
]
// This only adapts form fields; all validity checks are performed by Python.
function valueAt(row: unknown, path: (string | number)[]): string | number | null {
  let value = row as Record<string | number, unknown>
  for (const key of path) value = value[key] as typeof value
  return value as unknown as string | number | null
}
function edit(path: (string | number)[], event: Event, text = false) {
  const value = (event.target as HTMLInputElement).value
  const data = JSON.parse(JSON.stringify(props.instance))
  let target = data
  for (const key of path.slice(0, -1)) target = target[key]
  target[path.at(-1)!] = text ? value : value === '' ? null : Number(value)
  emit('change', data)
}
function rows(key: Collection, row?: unknown, index?: number) {
  const data = JSON.parse(JSON.stringify(props.instance))
  if (index !== undefined) data[key].splice(index, 1)
  else data[key].push(JSON.parse(JSON.stringify(row)))
  emit('change', data)
}
const generation = reactive({ width: 8, height: 6, robot_count: 3, task_count: 10, seed: 42 })
watch(() => props.instance, instance => {
  Object.assign(generation, { width: instance.width, height: instance.height, robot_count: instance.robots.length, task_count: instance.tasks.length, seed: instance.seed ?? 0 })
}, { immediate: true })
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('import', await file.text())
  input.value = ''
}
</script>

<template>
  <details class="panel editor" data-testid="editor">
    <summary>Créer / modifier l’instance</summary>
    <div class="fields">
      <label v-for="[key, label] in [['width', 'Largeur'], ['height', 'Hauteur'], ['horizon', 'Horizon'], ['seed', 'Graine (métadonnée)']]" :key="key">{{ label }}<input type="number" step="1" :data-testid="`edit-${key}`" :value="instance[key as 'width' | 'height' | 'horizon' | 'seed']" @input="edit([key!], $event)"></label>
    </div>
    <p class="hint">Échéance vide = aucune. Manutention à chaque extrémité. Indisponibilité : [début, fin exclue).</p>
    <details v-for="table in tables" :key="table.key" class="editor-table">
      <summary>{{ table.label }} <span class="badge">{{ instance[table.key].length }}</span></summary>
      <div class="table-scroll"><table><thead><tr><th v-for="column in table.columns" :key="column.label">{{ column.label }}</th><th>Action</th></tr></thead><tbody>
        <tr v-for="(row, index) in instance[table.key]" :key="index"><td v-for="column in table.columns" :key="column.label"><input :type="column.text ? 'text' : 'number'" step="1" :aria-label="`${table.label} ${index + 1} ${column.label}`" :value="valueAt(row, column.path)" @input="edit([table.key, index, ...column.path], $event, column.text)"></td><td><button :aria-label="`Supprimer ${table.label} ${index + 1}`" @click="rows(table.key, undefined, index)">Supprimer</button></td></tr>
      </tbody></table></div>
      <button @click="rows(table.key, table.row)">Ajouter {{ table.label }}</button>
    </details>
    <form class="generator" @submit.prevent="emit('generate', { ...generation, horizon: instance.horizon, obstacles: instance.obstacles, resources: instance.resources })">
      <h3>Génération aléatoire</h3>
      <div class="fields"><label v-for="[key, label] in [['width', 'Largeur du terrain'], ['height', 'Hauteur du terrain'], ['robot_count', 'Nombre de robots'], ['task_count', 'Nombre de tâches'], ['seed', 'Graine aléatoire']]" :key="key">{{ label }}<input v-model.number="generation[key as keyof typeof generation]" :data-testid="`generate-${key}`" type="number" step="1" :min="key === 'width' || key === 'height' ? 1 : 0" required></label></div>
      <p class="hint">Obstacles, stations et horizon conservés. Robots, tâches et indisponibilités remplacés. Faisabilité sans garantie d’achèvement.</p>
      <button :disabled="busy" data-testid="generate">Générer une instance</button>
    </form>
    <div class="fields"><label>Importer JSON<input type="file" accept=".json,application/json" data-testid="import-file" @change="importFile"></label><button :disabled="!valid" @click="emit('export')">Exporter l’instance JSON</button></div>
    <details><summary>Édition JSON / correction d’un import</summary><label>JSON de l’instance<textarea :value="jsonDraft" data-testid="json-draft" rows="12" spellcheck="false" @input="emit('draft', ($event.target as HTMLTextAreaElement).value)"></textarea></label><button :disabled="busy" data-testid="apply-json" @click="emit('import', jsonDraft)">Appliquer JSON</button></details>
  </details>
</template>
