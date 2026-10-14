export type Position = [number, number]
export type Strategy = 'fifo' | 'cpsat' | 'cnp'
export type TaskState = 'scheduled' | 'pending' | 'assigned' | 'picked_up' | 'completed'
export const taskLabels: Record<TaskState, string> = {
  scheduled: 'Non arrivée', pending: 'En attente', assigned: 'Affectée / collecte',
  picked_up: 'Collectée / livraison', completed: 'Terminée',
}
export const robotLabels: Record<string, string> = {
  idle: 'Disponible', to_pickup: 'Déplacement vers collecte', picking: 'Manutention de collecte',
  waiting_pickup: 'Attente de ressource à la collecte', to_delivery: 'Déplacement vers livraison',
  delivering: 'Manutention de livraison', waiting_delivery: 'Attente de ressource à la livraison',
}
export interface Instance {
  width: number; height: number; horizon: number; seed: number | null
  obstacles: Position[]
  robots: { id: string; position: Position; capacity: number }[]
  tasks: { id: string; pickup: Position; destination: Position; load: number; handling_ticks: number; deadline: number | null; arrival_tick: number }[]
  resources: { id: string; position: Position; capacity: number }[]
  unavailability: { robot_id: string; start_tick: number; end_tick: number }[]
}
export interface RobotSnapshot {
  position: Position; capacity: number; state: string; available: boolean; unavailable: boolean
  task_id: string | null; handling_remaining: number; resource_id: string | null; path: Position[]
}
export interface Snapshot {
  tick: number; finished: boolean; all_tasks_completed: boolean
  robots: Record<string, RobotSnapshot>
  tasks: Record<string, { state: TaskState; robot_id: string | null; arrival_tick: number; completed_tick: number | null; late: boolean }>
  resources: Record<string, { position: Position; capacity: number; holders: string[] }>
}
export interface TraceEvent {
  tick: number; kind: string; robot_id?: string; task_id?: string; resource_id?: string
  type?: string; sender?: string; recipient?: string; cost?: number; reason?: string; state?: string
  [key: string]: unknown
}
export type Metrics = Record<string, number | null | Record<string, number>>
export interface Result {
  trace: { instance: Instance; events: TraceEvent[]; snapshots: Snapshot[]; current: Snapshot; strategy: Strategy }
  metrics: Metrics
}
export interface Preview { instance: Instance; snapshot: Snapshot }
export interface Comparison {
  runs: { strategy: Strategy; status: string; error: string | null; [key: string]: unknown }[]
  [key: string]: unknown
}
