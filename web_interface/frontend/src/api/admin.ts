import { api } from './client'
import type { Stats, AdminUser, AdminRunsResponse, SystemConfig } from '../types'

export async function getAdminStats(): Promise<Stats> {
  return api.get<Stats>('/api/admin/stats')
}

export async function getAdminUsers(): Promise<AdminUser[]> {
  return api.get<AdminUser[]>('/api/admin/users')
}

export async function updateAdminUser(userId: number, updates: Record<string, unknown>): Promise<AdminUser> {
  return api.put<AdminUser>(`/api/admin/users/${userId}`, updates)
}

export async function getAdminRuns(params: string): Promise<AdminRunsResponse> {
  return api.get<AdminRunsResponse>(`/api/admin/runs?${params}`)
}

export async function getAdminConfig(): Promise<SystemConfig> {
  return api.get<SystemConfig>('/api/admin/config')
}

export async function updateAdminConfig(config: Partial<SystemConfig>): Promise<void> {
  await api.put('/api/admin/config', config)
}

export async function exportFeedbackCsv(): Promise<Response> {
  // Returns raw Response because this is CSV text, not JSON
  return api.getRaw('/api/admin/export/feedback')
}
