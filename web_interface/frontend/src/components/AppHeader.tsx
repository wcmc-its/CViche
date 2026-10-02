import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { HelpCircle, Menu, X } from 'lucide-react'
import { useCanViewAllRuns } from '../contexts/AuthContext'
import { useInbox } from '../contexts/InboxContext'
import { NARROW_HEADER_QUERY, useMediaQuery } from '../hooks/useMediaQuery'
import UserMenu from './UserMenu'

const tabClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center h-full px-1.5 sm:px-3.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
    isActive ? 'text-gray-900 border-ink' : 'text-gray-500 border-transparent hover:text-gray-900'
  }`

const menuItemClass = ({ isActive }: { isActive: boolean }) =>
  `block px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:bg-sand-100 hover:bg-sand-100 ${
    isActive ? 'text-gray-900 bg-sand-100' : 'text-gray-700'
  }`

/** "New run" with the count of emailed CVs waiting for confirmation (#1298); no badge at zero. */
function NewRunLabel() {
  const waiting = useInbox().items.length
  if (!waiting) return <>New run</>
  return (
    <span className="flex items-center gap-1.5">
      New run
      <span
        role="status"
        aria-label={`${waiting} emailed ${waiting === 1 ? 'CV' : 'CVs'} waiting`}
        className="min-w-[18px] rounded-full bg-primary-600 px-1.5 text-center text-xs font-semibold leading-[18px] text-white"
      >
        {waiting}
      </span>
    </span>
  )
}

interface NarrowMenuProps {
  showDashboard: boolean
  onRunPage: boolean
}

/** Below ~480px: the main tabs and Help behind one button. Escape or a pick closes it and
 *  returns focus to the button; opening moves focus to the first link. */
function NarrowMenu({ showDashboard, onRunPage }: NarrowMenuProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const { pathname } = useLocation()

  // Navigating (a link in the menu, or anything else) closes it.
  useEffect(() => { setOpen(false) }, [pathname])

  useEffect(() => {
    if (!open) return
    panelRef.current?.querySelector('a')?.focus()
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setOpen(false)
      buttonRef.current?.focus()
    }
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open])

  return (
    <div ref={rootRef} className="ml-auto">
      <button
        ref={buttonRef}
        type="button"
        aria-label="Main menu"
        aria-expanded={open}
        aria-controls="header-menu"
        onClick={() => setOpen((o) => !o)}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-gray-700 hover:bg-sand-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        {open ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
      </button>
      {open && (
        <div
          id="header-menu"
          ref={panelRef}
          className="absolute inset-x-0 top-full z-dropdown border-b border-sand-350 bg-sand-150 py-1 shadow-[0_12px_24px_rgba(60,40,10,0.12)]"
        >
          <nav aria-label="Main">
            <NavLink to="/runs" className={({ isActive }) => menuItemClass({ isActive: isActive || onRunPage })}>Runs</NavLink>
            <NavLink to="/" end className={menuItemClass}><NewRunLabel /></NavLink>
            {showDashboard && <NavLink to="/admin" className={menuItemClass}>Dashboard</NavLink>}
            <NavLink to="/help" className={menuItemClass}>Help</NavLink>
          </nav>
        </div>
      )}
    </div>
  )
}

/** Top bar on every signed-in page: logo, main tabs, Help, account (#1112). */
export default function AppHeader() {
  // Admin, or staff (read-only: the dashboard shows staff Feedback only).
  const showDashboard = useCanViewAllRuns()
  // A run page belongs under Runs even though its path is /run/:id.
  const onRunPage = useLocation().pathname.startsWith('/run/')
  const narrow = useMediaQuery(NARROW_HEADER_QUERY)

  return (
    <header role="banner" className="relative h-[61px] bg-sand-150 border-b border-sand-350">
      <div className="h-full px-4 md:px-7 flex items-center gap-2 sm:gap-6 md:gap-10">
        <Link to="/runs" aria-label="CViche home" className="shrink-0">
          <img src="/cviche-logo.png" alt="CViche" className="h-6 sm:h-10" />
        </Link>
        {narrow ? (
          <>
            <NarrowMenu showDashboard={showDashboard} onRunPage={onRunPage} />
            <UserMenu />
          </>
        ) : (
          <>
            <nav aria-label="Main" className="flex h-full min-w-0 overflow-x-auto [scrollbar-width:none]">
              <NavLink to="/runs" className={({ isActive }) => tabClass({ isActive: isActive || onRunPage })}>Runs</NavLink>
              <NavLink to="/" end className={tabClass}><NewRunLabel /></NavLink>
              {showDashboard && <NavLink to="/admin" className={tabClass}>Dashboard</NavLink>}
            </nav>
            <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-5">
              <Link
                to="/help"
                aria-label="Help and support"
                className="inline-flex items-center gap-1.5 text-sm text-gray-700 hover:text-gray-900"
              >
                <HelpCircle className="h-4 w-4" aria-hidden="true" />
                <span className="hidden sm:inline">Help</span>
              </Link>
              <UserMenu />
            </div>
          </>
        )}
      </div>
    </header>
  )
}
