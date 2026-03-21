import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { BarChart3, Users, DollarSign, MessageSquare, ArrowLeft, Loader2 } from 'lucide-react'
import AdminUsers from './AdminUsers'
import AdminSubmissions from './AdminSubmissions'
import AdminFeedbackInsights from './AdminFeedbackInsights'
import AdminConfig from './AdminConfig'

interface Stats {
  total_runs: number
  active_users: number
  total_cost: number
  feedback_rate: number
}

const TABS = ['Users', 'All Submissions', 'Feedback Insights', 'Config'] as const
type TabName = typeof TABS[number]

export default function AdminDashboard() {
  const navigate = useNavigate()
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<TabName>('Users')

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/admin/stats')
      if (res.ok) {
        setStats(await res.json())
      }
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
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header
        className="border-b border-gray-200"
        style={{
          backgroundImage: 'url(/headerbg.png)',
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="text-gray-400 hover:text-gray-600 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 rounded-lg p-1.5"
              aria-label="Back to upload page"
            >
              <ArrowLeft className="h-5 w-5" />
            </button>
            <div>
              <h1 className="text-lg font-semibold text-gray-900">Admin Dashboard</h1>
              <p className="text-xs text-gray-400">CViche Pipeline Management</p>
            </div>
          </div>
          <img
            src="/header-logo.png"
            alt="CViche"
            className="h-8 object-contain hidden sm:block"
          />
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Stat Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-8">
          <StatCard
            icon={<BarChart3 className="h-4 w-4" />}
            iconBg="bg-blue-50 text-blue-600"
            label="Total Runs"
            value={stats?.total_runs.toLocaleString() ?? '0'}
          />
          <StatCard
            icon={<Users className="h-4 w-4" />}
            iconBg="bg-emerald-50 text-emerald-600"
            label="Active Users"
            value={stats?.active_users.toLocaleString() ?? '0'}
            sublabel="Last 30 days"
          />
          <StatCard
            icon={<DollarSign className="h-4 w-4" />}
            iconBg="bg-amber-50 text-amber-600"
            label="Total Cost"
            value={`$${stats?.total_cost.toFixed(2) ?? '0.00'}`}
          />
          <StatCard
            icon={<MessageSquare className="h-4 w-4" />}
            iconBg="bg-purple-50 text-purple-600"
            label="Feedback Rate"
            value={`${stats?.feedback_rate.toFixed(1) ?? '0.0'}%`}
            sublabel="of completed runs"
          />
        </div>

        {/* Tabs */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="border-b border-gray-200 px-4 sm:px-6">
            <nav className="-mb-px flex space-x-1 overflow-x-auto" aria-label="Admin tabs">
              {TABS.map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`whitespace-nowrap py-3 px-4 border-b-2 text-sm font-medium transition-colors focus:outline-none focus-visible:text-primary-700 ${
                    activeTab === tab
                      ? 'border-primary-600 text-primary-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                  aria-current={activeTab === tab ? 'page' : undefined}
                >
                  {tab}
                </button>
              ))}
            </nav>
          </div>

          {/* Tab Content */}
          <div className="p-4 sm:p-6">
            {activeTab === 'Users' && <AdminUsers />}
            {activeTab === 'All Submissions' && <AdminSubmissions />}
            {activeTab === 'Feedback Insights' && <AdminFeedbackInsights />}
            {activeTab === 'Config' && <AdminConfig />}
          </div>
        </div>
      </main>
    </div>
  )
}

function StatCard({
  icon,
  iconBg,
  label,
  value,
  sublabel,
}: {
  icon: React.ReactNode
  iconBg: string
  label: string
  value: string
  sublabel?: string
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 sm:p-5">
      <div className="flex items-center gap-2.5 mb-3">
        <div className={`p-1.5 rounded-lg ${iconBg}`}>{icon}</div>
        <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-2xl font-bold text-gray-900 tabular-nums">{value}</p>
      {sublabel && (
        <p className="text-xs text-gray-400 mt-0.5">{sublabel}</p>
      )}
    </div>
  )
}
