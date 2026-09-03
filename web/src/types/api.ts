export interface ApiEnvelope<T = unknown> {
  code?: number
  msg?: string
  status?: string
  detail?: string
  data?: T
}

export interface Project {
  id: number
  name: string
  status?: string
  workspace_abs_path?: string
  created_at?: string
  updated_at?: string
  role?: string
}

export interface SessionItem {
  session_id: string
  user_id?: number
  project_id?: number
  title?: string | null
  workspace_abs_path?: string
  created_at?: string
  updated_at?: string
}

export interface WorkspaceTreeNode {
  name: string
  relative_path: string
  type: 'file' | 'directory'
  size?: number
  children?: WorkspaceTreeNode[]
}

export type SseEventType =
  | 'orchestrator'
  | 'planner'
  | 'coder'
  | 'worker'
  | 'report_chunk'
  | 'error'
  | 'streaming_error'
  | 'streaming_ended'
  | string

export interface SsePayload {
  type: SseEventType
  data?: unknown
  content?: string
  message?: string
  error?: string
  timestamp?: string
}
