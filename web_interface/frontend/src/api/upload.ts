import { api } from './client'
import type { Estimate } from '../types'

export async function getEstimate(file: File): Promise<Estimate> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<Estimate>('/api/estimate', formData)
}

export async function uploadFile(file: File): Promise<{ run_id: string }> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<{ run_id: string }>('/api/upload', formData)
}
