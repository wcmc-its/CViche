import { api } from './client'
import { authRoutes } from './routes'
import type { User, AuthConfig } from '../types'

export async function getAuthConfig(): Promise<AuthConfig> {
  return api.get<AuthConfig>(authRoutes.config())
}

export async function getCurrentUser(): Promise<User> {
  return api.get<User>(authRoutes.me())
}

export async function login(email: string, displayName: string): Promise<void> {
  await api.post(authRoutes.login(), { email, display_name: displayName })
}

export async function logout(): Promise<void> {
  await api.post(authRoutes.logout())
}
