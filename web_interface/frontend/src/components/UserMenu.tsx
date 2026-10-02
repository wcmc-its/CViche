import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'

interface UserMenuProps {
  /** Extra classes for the wrapper, e.g. to position the trigger. */
  className?: string
}

/**
 * Account control: a small icon button that opens a popover showing the
 * signed-in user and a Sign out action. Used in the upload/pipeline headers
 * and the consent page so a user can always end their session (#72).
 */
export default function UserMenu({ className }: UserMenuProps) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!user) return null

  const handleLogout = async () => {
    setSigningOut(true)
    try {
      await logout()
    } catch {
      // Even if the server call fails, send the user to /login; the route
      // guard re-gates on the cleared (or soon-cleared) auth state.
    } finally {
      navigate('/login', { replace: true })
    }
  }

  return (
    <div ref={menuRef} className={`relative ${className ?? ''}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Account menu"
        aria-haspopup="menu"
        aria-expanded={open}
        title={user.email}
        className="flex items-center gap-2.5 rounded-lg p-1 text-left focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
      >
        <span className="leading-tight">
          <span className="block text-sm font-medium text-gray-900">{user.display_name}</span>
          <span className="block text-xs text-gray-500">{user.role === 'admin' ? 'Admin' : 'Member'}</span>
        </span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="absolute right-0 mt-2 w-60 rounded-lg border border-gray-200 bg-white shadow-lg z-50 overflow-hidden"
        >
          <div className="px-4 py-3 border-b border-gray-100">
            <p className="text-sm font-medium text-gray-900 truncate">{user.display_name}</p>
            <p className="text-xs text-gray-500 truncate">{user.email}</p>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={handleLogout}
            disabled={signingOut}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed focus:bg-gray-50 focus-visible:outline-none transition-colors"
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
            {signingOut ? 'Signing out...' : 'Sign out'}
          </button>
        </div>
      )}
    </div>
  )
}
