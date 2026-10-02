import type { SystemConfig } from '../types'

/** True when the Run limits inputs differ from what is saved, so the Save button has something to save. */
export function limitsChanged(config: Pick<SystemConfig, 'rate_limit_daily' | 'rate_limit_monthly'>, daily: string, monthly: string): boolean {
  return daily !== String(config.rate_limit_daily) || monthly !== String(config.rate_limit_monthly)
}
