import { apiClient } from './client'
import type { ApiEnvelope, SessionItem, WorkspaceTreeNode } from '@/types/api'

export async function listSessions(projectId: number): Promise<SessionItem[]> {
  const { data } = await apiClient.get<ApiEnvelope<{ sessions: SessionItem[] }>>(
    '/session/list',
    { params: { project_id: projectId } },
  )
  return data.data?.sessions ?? []
}

export async function createSession(projectId: number): Promise<SessionItem> {
  const { data } = await apiClient.post<
    ApiEnvelope<{
      session_id: string
      user_id: number
      project_id: number
      workspace_abs_path: string
    }>
  >('/session/create', { project_id: projectId })
  if (!data.data?.session_id) {
    throw new Error(data.msg || '创建会话失败')
  }
  return {
    session_id: data.data.session_id,
    user_id: data.data.user_id,
    project_id: data.data.project_id,
    workspace_abs_path: data.data.workspace_abs_path,
  }
}

export async function getWorkspaceTree(sessionId: string): Promise<WorkspaceTreeNode[]> {
  const { data } = await apiClient.get<
    ApiEnvelope<{ tree: WorkspaceTreeNode; files?: unknown[] }>
  >('/session/workspace-tree', { params: { session_id: sessionId } })
  const root = data.data?.tree
  if (!root) return []
  return root.children ?? []
}

export async function uploadSessionFile(sessionId: string, file: File): Promise<void> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('file', file)
  await apiClient.post('/session/upload-excel', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
