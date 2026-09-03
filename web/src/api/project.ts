import { apiClient } from './client'
import type { ApiEnvelope, Project } from '@/types/api'

export async function listProjects(): Promise<Project[]> {
  const { data } = await apiClient.get<ApiEnvelope<{ projects: Project[] }>>('/project/list')
  return data.data?.projects ?? []
}

export async function createProject(name: string): Promise<Project> {
  const { data } = await apiClient.post<ApiEnvelope<Project>>('/project/create', { name })
  if (!data.data) {
    throw new Error(data.msg || '创建项目失败')
  }
  return data.data
}

export async function getProject(projectId: number): Promise<Project> {
  const { data } = await apiClient.get<ApiEnvelope<Project>>(`/project/${projectId}`)
  if (!data.data) {
    throw new Error(data.msg || '获取项目失败')
  }
  return data.data
}
