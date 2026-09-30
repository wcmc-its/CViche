import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import type { Stats } from '../types'
import { getAdminStats } from '../api/admin'
import AdminOverview from './AdminOverview'
import AdminUsers from './AdminUsers'
import AdminSubmissions from './AdminSubmissions'
import AdminFeedbackInsights from './AdminFeedbackInsights'
import AdminConfig from './AdminConfig'

const TABS = ['Overview', 'Runs', 'Feedback', 'Settings'] as const
type TabName = typeof TABS[number]

export default function AdminDashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<TabName>('Overview')

  const fetchStats = async () => {
    try {
      setStats(await getAdminStats())
    } catch (err) {
      console.error('Failed to fetch admin stats:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStats()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-8">
        <h1 className="text-[26px] font-semibold text-gray-900">Dashboard</h1>
        <nav className="mt-5 flex gap-1 overflow-x-auto border-b border-sand-350" aria-label="Admin tabs">
          {TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`-mb-px whitespace-nowrap px-3 py-2 border-b-2 text-sm transition-colors focus:outline-none focus-visible:text-gray-900 ${
                activeTab === tab
                  ? 'border-ink text-gray-900 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
              aria-current={activeTab === tab ? 'page' : undefined}
            >
              {tab}
            </button>
          ))}
        </nav>
      </div>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {activeTab === 'Overview' && (
          <AdminOverview stats={stats} onOpenFeedback={() => setActiveTab('Feedback')} />
        )}
        {activeTab === 'Runs' && <AdminSubmissions />}
        {activeTab === 'Feedback' && <AdminFeedbackInsights />}
        {activeTab === 'Settings' && (
          <div className="space-y-8">
            <section aria-labelledby="settings-users-heading">
              <h2 id="settings-users-heading" className="text-[15px] font-semibold text-gray-900 mb-3">Users</h2>
              <AdminUsers />
            </section>
            <section aria-labelledby="settings-config-heading">
              <h2 id="settings-config-heading" className="text-[15px] font-semibold text-gray-900 mb-3">Configuration</h2>
              <AdminConfig />
            </section>
          </div>
        )}
      </main>
    </div>
  )
}
