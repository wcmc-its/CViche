import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import { matchesQuery } from './runFilterOptions'
import type { ComboModel, ComboOption } from './runFilterOptions'

/** Most matches listed at once; past this the footer asks for a narrower search. */
const LIST_LIMIT = 8

interface RunFilterComboProps {
  /** Button caption, e.g. "Department". */
  label: string
  /** Text shown for the current selection, e.g. "All" or a name. */
  valueLabel: string
  /** Selected option id; '' when the filter is unset. */
  activeId: string
  placeholder: string
  model: ComboModel
  onPick: (id: string) => void
}

interface Visible {
  pins: ComboOption[]
  shown: ComboOption[]
  matchCount: number
}

function visibleOptions(model: ComboModel, query: string): Visible {
  const matches = model.items.filter((item) => matchesQuery(item, query))
  return {
    pins: query ? [] : model.pinned,
    shown: matches.slice(0, LIST_LIMIT),
    matchCount: matches.length,
  }
}

interface OptionRowProps {
  id: string
  option: ComboOption
  active: boolean
  highlighted: boolean
  onPick: () => void
  onHover: () => void
}

function OptionRow({ id, option, active, highlighted, onPick, onHover }: OptionRowProps) {
  return (
    <li
      id={id}
      role="option"
      aria-selected={active}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onPick}
      onMouseEnter={onHover}
      className={`grid grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-2 px-2 py-[7px] rounded-md cursor-pointer ${
        highlighted ? 'bg-sand-50' : active ? 'bg-primary-50' : ''
      }`}
    >
      <span className="flex">{active && <Check className="w-3.5 h-3.5 text-primary-700" aria-hidden="true" />}</span>
      <span className="min-w-0 flex flex-col">
        <span className="text-[13px] font-medium text-gray-900 break-words">{option.label}</span>
        {option.meta && <span className="text-xs text-gray-500 break-words">{option.meta}</span>}
      </span>
      <span className="text-xs text-gray-500 tabular-nums">{option.count ?? ''}</span>
    </li>
  )
}

/** Searchable single-select popover (combobox + listbox) for one admin run filter. */
export default function RunFilterCombo({ label, valueLabel, activeId, placeholder, model, onPick }: RunFilterComboProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const uid = useId()
  const listId = `${uid}-list`
  const optionId = (index: number) => `${uid}-opt-${index}`

  const { pins, shown, matchCount } = useMemo(() => visibleOptions(model, query.trim().toLowerCase()), [model, query])
  const flat = [...pins, ...shown]

  const close = (returnFocus: boolean) => {
    setOpen(false)
    setQuery('')
    if (returnFocus) buttonRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close(false)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [open])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  const pick = (option: ComboOption) => {
    onPick(option.id)
    close(true)
  }

  const onInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      close(true)
    } else if (e.key === 'ArrowDown' && flat.length > 0) {
      e.preventDefault()
      setHighlight((h) => (h + 1) % flat.length)
    } else if (e.key === 'ArrowUp' && flat.length > 0) {
      e.preventDefault()
      setHighlight((h) => (h - 1 + flat.length) % flat.length)
    } else if (e.key === 'Enter' && flat[highlight]) {
      e.preventDefault()
      pick(flat[highlight])
    }
  }

  const renderRows = (options: ComboOption[], offset: number) =>
    options.map((option, i) => (
      <OptionRow
        key={`${option.id}-${option.label}`}
        id={optionId(offset + i)}
        option={option}
        active={option.id === activeId}
        highlighted={offset + i === highlight}
        onPick={() => pick(option)}
        onHover={() => setHighlight(offset + i)}
      />
    ))

  const sectionTitle = (title: string) => (
    <li role="presentation" className="px-2 pt-2 pb-1 text-[11px] font-semibold text-gray-500 tracking-wider uppercase">
      {title}
    </li>
  )

  return (
    <div ref={rootRef} className="relative flex-none">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o)
          setQuery('')
          setHighlight(0)
        }}
        className={`flex h-9 max-w-[260px] items-center gap-1.5 whitespace-nowrap rounded-lg border bg-white pl-3 pr-2.5 text-[13px] text-gray-500 hover:border-sand-400 focus:outline-none focus:ring-2 focus:ring-primary-500 ${
          activeId ? 'border-ink' : 'border-sand-400'
        }`}
      >
        <span>{label}:</span>
        <span className="truncate font-medium text-gray-900">{valueLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 flex-none text-gray-500" aria-hidden="true" />
      </button>
      {open && (
        <div className="absolute right-0 top-[42px] z-dropdown w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-[10px] border border-sand-300 bg-white shadow-[0_12px_32px_rgba(60,40,10,0.14)]">
          <div className="flex h-[42px] items-center gap-2 border-b border-sand-200 px-3">
            <Search className="h-[15px] w-[15px] text-gray-500" aria-hidden="true" />
            <input
              ref={inputRef}
              type="text"
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={flat.length > 0 ? optionId(highlight) : undefined}
              aria-autocomplete="list"
              aria-label={`${label} filter search`}
              value={query}
              placeholder={placeholder}
              onChange={(e) => {
                setQuery(e.target.value)
                setHighlight(0)
              }}
              onKeyDown={onInputKeyDown}
              className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-gray-400"
            />
          </div>
          <ul id={listId} role="listbox" aria-label={label} className="max-h-[340px] overflow-auto p-1.5">
            {renderRows(pins, 0)}
            {shown.length > 0 && sectionTitle(query.trim() ? 'Matches' : 'All')}
            {renderRows(shown, pins.length)}
            {flat.length === 0 && (
              <li role="presentation" className="px-2 py-3.5 text-[13px] text-gray-500">
                No matches. Search by name, CWID or email.
              </li>
            )}
          </ul>
          {matchCount > LIST_LIMIT && (
            <div className="border-t border-sand-200 bg-sand-50 px-3.5 py-2 text-xs text-gray-500">
              Showing {LIST_LIMIT} of {matchCount}. Keep typing to narrow.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
