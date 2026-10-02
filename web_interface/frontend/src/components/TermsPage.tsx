import { Loader2, ShieldCheck } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import ConsentTextRenderer from './shared/ConsentTextRenderer'

/** The consent terms the user agreed to, read-only. The text is the one the consent page shows
 *  (GET /api/consent), rendered by the same component: nothing here restates it. */
export default function TermsPage() {
  const { consentStatus } = useAuth()

  if (!consentStatus) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" aria-label="Loading" />
      </div>
    )
  }

  return (
    <main className="px-4 py-8">
      <div className="mx-auto w-full max-w-[760px]">
        <h1 className="flex items-center gap-2.5 text-[26px] font-semibold text-gray-900">
          <ShieldCheck className="h-6 w-6 text-primary-600" aria-hidden="true" />
          Terms you agreed to
        </h1>
        <p className="mb-5 mt-1 text-sm text-gray-600">Consent to Participate, version {consentStatus.version}</p>
        <section aria-label="Consent text" className="rounded-xl border border-sand-300 bg-white p-5 shadow-[0_1px_2px_rgba(60,40,10,0.05)] sm:p-6">
          <div className="prose prose-sm prose-gray max-w-none">
            <ConsentTextRenderer text={consentStatus.text} />
          </div>
        </section>
      </div>
    </main>
  )
}
