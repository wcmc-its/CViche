import { Link, NavLink, useLocation } from 'react-router-dom'
import { HelpCircle } from 'lucide-react'
import { useCanViewAllRuns } from '../contexts/AuthContext'
import UserMenu from './UserMenu'

const tabClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center h-full px-1.5 sm:px-3.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
    isActive ? 'text-gray-900 border-ink' : 'text-gray-500 border-transparent hover:text-gray-900'
  }`

/** Top bar on every signed-in page: logo, main tabs, Help, account (#1112). */
export default function AppHeader() {
  // Admin, or staff (read-only: the dashboard shows staff Feedback only).
  const showDashboard = useCanViewAllRuns()
  // A run page belongs under Runs even though its path is /run/:id.
  const onRunPage = useLocation().pathname.startsWith('/run/')

  return (
    <header role="banner" className="h-[61px] bg-sand-150 border-b border-sand-350">
      <div className="h-full px-4 md:px-7 flex items-center gap-2 sm:gap-6 md:gap-10">
        <Link to="/runs" aria-label="CViche home" className="shrink-0">
          <img src="/cviche-logo.png" alt="CViche" className="h-6 sm:h-10" />
        </Link>
        <nav aria-label="Main" className="flex h-full min-w-0 overflow-x-auto [scrollbar-width:none]">
          <NavLink to="/runs" className={({ isActive }) => tabClass({ isActive: isActive || onRunPage })}>Runs</NavLink>
          <NavLink to="/" end className={tabClass}>New run</NavLink>
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
      </div>
    </header>
  )
}
