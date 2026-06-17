import { useEffect, useRef } from 'react'

// Default inactivity window before auto-logout. Tunable via
// VITE_IDLE_TIMEOUT_MINUTES (build-time env); falls back to 20 minutes.
const DEFAULT_IDLE_MINUTES = 20

// User-interaction events that count as "active". Pointer-move / scroll fire
// rapidly, so the handler is throttled below to avoid resetting on every pixel.
const ACTIVITY_EVENTS: (keyof WindowEventMap)[] = [
  'mousemove',
  'mousedown',
  'keydown',
  'scroll',
  'touchstart',
  'click',
]

function resolveTimeoutMs(): number {
  const raw = Number(import.meta.env.VITE_IDLE_TIMEOUT_MINUTES)
  const minutes = Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_IDLE_MINUTES
  return minutes * 60_000
}

/**
 * Sign the user out after a period of no interaction.
 *
 * Sessions are stateless, signed cookies with a long absolute TTL and NO
 * server-side idle window (see AuthContext and backend app/auth.py), so an
 * "idle timeout" is enforced here on the client: any real user activity resets
 * the timer; when it elapses, `onIdle` runs (the normal logout path, which
 * clears the cookie + in-memory auth and bounces to /login). Armed only while
 * `active` (i.e. signed in), so it never fires on the login screen.
 *
 * This is a usability / unattended-screen control, not a server-enforced
 * revocation (stateless cookies can't be revoked before their TTL -- that is
 * tracked separately in #110/#127).
 */
export function useIdleLogout(active: boolean, onIdle: () => void): void {
  // Hold the latest callback in a ref so re-renders don't re-arm the listeners.
  const onIdleRef = useRef(onIdle)
  onIdleRef.current = onIdle

  useEffect(() => {
    if (!active) return

    const timeoutMs = resolveTimeoutMs()
    let timer: ReturnType<typeof setTimeout>
    let lastReset = 0

    const fire = () => onIdleRef.current()

    const reset = () => {
      // Throttle resets to at most once a second; high-frequency events
      // (mousemove/scroll) otherwise churn clearTimeout/setTimeout needlessly.
      const now = Date.now()
      if (now - lastReset < 1000) return
      lastReset = now
      clearTimeout(timer)
      timer = setTimeout(fire, timeoutMs)
    }

    timer = setTimeout(fire, timeoutMs)
    for (const ev of ACTIVITY_EVENTS) {
      window.addEventListener(ev, reset, { passive: true })
    }

    return () => {
      clearTimeout(timer)
      for (const ev of ACTIVITY_EVENTS) {
        window.removeEventListener(ev, reset)
      }
    }
  }, [active])
}
