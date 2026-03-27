import { api } from './client'
import type { User, AuthConfig } from '../types'

export async function getAuthConfig(): Promise<AuthConfig> {
  return api.get<AuthConfig>('/api/auth/config')
}

export async function getCurrentUser(): Promise<User> {
  return api.get<User>('/api/auth/me')
}

export async function login(email: string, displayName: string): Promise<void> {
  await api.post('/api/auth/login', { email, display_name: displayName })
}

export async function logout(): Promise<void> {
  await api.post('/api/auth/logout')
}
