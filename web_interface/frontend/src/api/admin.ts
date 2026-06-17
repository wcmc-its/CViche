import { api } from './client'
import { adminRoutes } from './routes'
import type { Stats, AdminUser, AdminRunsResponse, QualityScoreResult, SystemConfig } from '../types'

export async function getAdminStats(): Promise<Stats> {
  return api.get<Stats>(adminRoutes.stats())
}

export async function getAdminUsers(): Promise<AdminUser[]> {
  return api.get<AdminUser[]>(adminRoutes.users())
}

export async function updateAdminUser(userId: number, updates: Record<string, unknown>): Promise<AdminUser> {
  return api.put<AdminUser>(adminRoutes.user(userId), updates)
}

export async function getAdminRuns(params: string): Promise<AdminRunsResponse> {
  return api.get<AdminRunsResponse>(adminRoutes.runs(params))
}

export async function computeRunScore(runId: string): Promise<QualityScoreResult> {
  return api.post<QualityScoreResult>(adminRoutes.runScore(runId))
}

export async function getAdminConfig(): Promise<SystemConfig> {
  return api.get<SystemConfig>(adminRoutes.config())
}

export async function updateAdminConfig(config: Partial<SystemConfig>): Promise<void> {
  await api.put(adminRoutes.config(), config)
}

export async function exportFeedbackCsv(): Promise<Response> {
  // Returns raw Response because this is CSV text, not JSON
  return api.getRaw(adminRoutes.exportFeedback())
}

export async function deleteFeedback(feedbackId: number): Promise<void> {
  await api.delete(adminRoutes.feedback(feedbackId))
}
