import { useState, FormEvent } from 'react'
import { Loader2, UserPlus } from 'lucide-react'

interface AddUserFormProps {
  /** Saves the (trimmed, lower-cased) address; true clears the input, false keeps it for correction. */
  onAdd: (email: string) => Promise<boolean>
}

/** Add-by-email input shown at the top of the Users panel. */
export default function AddUserForm({ onAdd }: AddUserFormProps) {
  const [email, setEmail] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const trimmed = email.trim().toLowerCase()
    if (!trimmed) return
    setSaving(true)
    try {
      if (await onAdd(trimmed)) setEmail('')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex gap-2 p-4 border-b border-sand-200">
      <label className="sr-only" htmlFor="new-user-email">New user email</label>
      <input
        id="new-user-email"
        type="email"
        placeholder="user@med.cornell.edu"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="flex-1 min-w-0 text-sm border border-gray-300 rounded-lg px-3 py-2 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
      />
      <button
        type="submit"
        disabled={!email.trim() || saving}
        className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        {saving ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <UserPlus className="w-4 h-4" aria-hidden="true" />}
        Add user
      </button>
    </form>
  )
}
