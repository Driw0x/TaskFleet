import type { Instance, Position, Result } from './types'

export type Milestone = 'm7' | 'm8' | 'm9' | 'm10'
export type ScientificData = Record<string, unknown>
export interface MapfProblem {
  width: number; height: number; horizon: number; obstacles: Position[]
  agents: { id: string; start: Position; goal: Position }[]
}
export interface CatalogEntry<T> { id: string; label: string; instance: T; milestones?: Milestone[] }
export interface ExperimentCatalog {
  warehouse_instances: CatalogEntry<Instance>[]
  mapf_instances: CatalogEntry<MapfProblem>[]
  scenario_sets: { id: string; label: string; scenarios: unknown[] }[]
  checkpoints: { id: string; label: string; source: string }[]
  training_sets: { id: string; label: string }[]
  validation_sets: { id: string; label: string }[]
  selected_models: { m8: string; m10: string }
  limits: ScientificData
  incompatible_instances?: { id: string; label: string; format: 'warehouse' | 'mapf'; reason: string }[]
}
export interface ScientificResult extends ScientificData {
  milestone: Milestone; method: string; execution_status: string
  metrics: ScientificData; trace?: Result['trace']
  plan?: ScientificData; execution?: ScientificData; rolling?: ScientificData
  robust?: ScientificData; scenario_runs?: ScientificData[]
  problem?: MapfProblem; coordination?: ScientificData; episode?: ScientificData
  training?: ScientificData; checkpoint?: string
  comparison?: (ScientificData & { method: string; execution_status: string; metrics: ScientificData; plan?: ScientificData })[]
}
export interface ExperimentJob {
  id: string; milestone: Milestone; method: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  phase: string; message: string; result: ScientificResult | null
  error: { kind: string; message: string } | null
}
export interface OllamaStatus {
  available: boolean
  models: { name: string; digest?: string }[]
  selected_models: { m8: string; m10: string }
  errors: unknown[]
}

export function data(value: unknown): ScientificData {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as ScientificData : {}
}
export function list(value: unknown): unknown[] { return Array.isArray(value) ? value : [] }
export function display(value: unknown): string {
  return value === null || value === undefined ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)
}
