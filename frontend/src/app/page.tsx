'use client'

import { useEffect, useState } from 'react'
import { Activity, TrendingUp, Sparkles, Loader2 } from 'lucide-react'
import SummaryCards from '@/components/SummaryCards'
import SegmentChart from '@/components/SegmentChart'
import NarrativePanel from '@/components/NarrativePanel'
import TrendView from '@/components/TrendView'
import { streamAnalysis } from '@/lib/stream'
import { PRODUCT_LABELS, ALL_PRODUCTS } from '@/lib/constants'
import type { AnomalyEvent, Decomposition } from '@/lib/types'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

type Tab    = 'trend' | 'explain'
type Status = 'idle' | 'decomposing' | 'narrating' | 'done' | 'error'

function severityLabel(z: number): { label: string; className: string } {
  if (z >= 7) return { label: 'Critical', className: 'bg-red-100 text-red-700' }
  if (z >= 5) return { label: 'High',     className: 'bg-orange-100 text-orange-700' }
  if (z >= 3) return { label: 'Medium',   className: 'bg-yellow-100 text-yellow-700' }
  return           { label: 'Low',        className: 'bg-green-100 text-green-700' }
}

function formatDateRange(start: string, end: string): string {
  const fmt = (s: string) =>
    new Date(s + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  return start === end ? fmt(start) : `${fmt(start)} – ${fmt(end)}`
}

export default function Page() {
  const [tab, setTab]             = useState<Tab>('trend')
  const [events, setEvents]       = useState<AnomalyEvent[]>([])
  const [loadingEvents, setLoadingEvents] = useState(true)
  const [filter, setFilter]       = useState<string>('all')
  const [selected, setSelected]   = useState<AnomalyEvent | null>(null)
  const [status, setStatus]       = useState<Status>('idle')
  const [decomp, setDecomp]       = useState<Decomposition | null>(null)
  const [narrative, setNarrative] = useState('')
  const [errorMsg, setErrorMsg]   = useState('')

  useEffect(() => {
    fetch(`${API}/events`)
      .then(r => r.json())
      .then((data: AnomalyEvent[]) => { setEvents(data); setLoadingEvents(false) })
      .catch(() => { setErrorMsg('Could not reach the backend. Is it running on port 8000?'); setLoadingEvents(false) })
  }, [])

  const filtered = filter === 'all' ? events : events.filter(e => e.product === filter)
  const busy     = status === 'decomposing' || status === 'narrating'

  function handleSelect(ev: AnomalyEvent) {
    if (busy) return
    setSelected(ev)
    setDecomp(null)
    setNarrative('')
    setStatus('idle')
    setErrorMsg('')
  }

  async function handleAnalyze() {
    if (!selected || busy) return
    setStatus('decomposing')
    setDecomp(null)
    setNarrative('')
    setErrorMsg('')

    await streamAnalysis(selected.product, selected.start, selected.end, {
      onDecomposition(data) { setDecomp(data); setStatus('narrating') },
      onChunk(text)         { setNarrative(prev => prev + text) },
      onDone()              { setStatus('done') },
      onError(msg)          { setErrorMsg(msg); setStatus('error') },
    })
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center gap-3">
          <div className="bg-violet-600 p-1.5 rounded-lg">
            <Activity size={18} className="text-white" />
          </div>
          <div className="flex-1">
            <h1 className="text-base font-bold text-slate-900 leading-none">Anomaly Explainer</h1>
            <p className="text-xs text-slate-500 mt-0.5">AI-powered payment TPV root cause analysis</p>
          </div>
          <nav className="flex gap-1 bg-slate-100 rounded-lg p-1">
            {(['trend', 'explain'] as Tab[]).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  tab === t ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                }`}>
                {t === 'trend' ? <TrendingUp size={14} /> : <Sparkles size={14} />}
                {t === 'trend' ? 'Trend' : 'Explainer'}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-8">

        {tab === 'trend' && <TrendView />}

        {tab === 'explain' && (
          <div className="flex gap-6 items-start">

            {/* ── Sidebar ── */}
            <aside className="w-72 flex-shrink-0">
              {/* Product filter */}
              <div className="flex flex-wrap gap-1 mb-3">
                <button onClick={() => setFilter('all')}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                    filter === 'all' ? 'bg-violet-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:border-violet-300'
                  }`}>
                  All ({events.length})
                </button>
                {ALL_PRODUCTS.map(p => {
                  const count = events.filter(e => e.product === p).length
                  if (!count) return null
                  return (
                    <button key={p} onClick={() => setFilter(p)}
                      className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                        filter === p ? 'bg-violet-600 text-white' : 'bg-white border border-slate-200 text-slate-600 hover:border-violet-300'
                      }`}>
                      {PRODUCT_LABELS[p]} ({count})
                    </button>
                  )
                })}
              </div>

              {/* Cards */}
              <div className="space-y-2 max-h-[calc(100vh-220px)] overflow-y-auto pr-1">
                {loadingEvents && (
                  <div className="flex items-center gap-2 text-slate-500 text-sm py-6 justify-center">
                    <Loader2 size={14} className="animate-spin" /> Loading anomalies...
                  </div>
                )}
                {filtered.map(ev => {
                  const sev      = severityLabel(ev.peak_z)
                  const isActive = selected?.id === ev.id
                  return (
                    <button key={ev.id} onClick={() => handleSelect(ev)}
                      className={`w-full text-left rounded-xl border p-3.5 transition-all ${
                        isActive
                          ? 'bg-violet-50 border-violet-300 shadow-sm'
                          : 'bg-white border-slate-200 hover:border-violet-200 hover:shadow-sm'
                      } ${busy ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-semibold text-slate-800">
                          {PRODUCT_LABELS[ev.product] ?? ev.product}
                        </span>
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${sev.className}`}>
                          {sev.label}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 mb-1.5">
                        {formatDateRange(ev.start, ev.end)}
                      </p>
                      <span className={`inline-flex items-center gap-1 text-xs font-medium ${
                        ev.direction === 'spike' ? 'text-red-600' : 'text-emerald-600'
                      }`}>
                        {ev.direction === 'spike' ? '↑ Volume spike' : '↓ Volume drop'}
                      </span>
                    </button>
                  )
                })}
              </div>
            </aside>

            {/* ── Main panel ── */}
            <main className="flex-1 min-w-0 space-y-5">
              {!selected && (
                <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-16 text-center">
                  <div className="text-4xl mb-4">📊</div>
                  <h2 className="text-base font-semibold text-slate-700 mb-1">Select an anomaly to investigate</h2>
                  <p className="text-sm text-slate-400 max-w-sm mx-auto">
                    Click any anomaly on the left. The AI will explain what caused it and what to do next.
                  </p>
                </div>
              )}

              {selected && (
                <>
                  {/* Event header + Analyze button */}
                  <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <h2 className="text-base font-bold text-slate-900">
                            {PRODUCT_LABELS[selected.product] ?? selected.product}
                          </h2>
                          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${severityLabel(selected.peak_z).className}`}>
                            {severityLabel(selected.peak_z).label}
                          </span>
                        </div>
                        <p className="text-sm text-slate-500">{formatDateRange(selected.start, selected.end)}</p>
                        <p className={`text-sm font-medium mt-1 ${selected.direction === 'spike' ? 'text-red-600' : 'text-emerald-600'}`}>
                          {selected.direction === 'spike' ? '↑ Volume spike' : '↓ Volume drop'}
                        </p>
                      </div>
                      <button onClick={handleAnalyze} disabled={busy}
                        className="px-5 py-2.5 bg-violet-600 hover:bg-violet-700 disabled:bg-violet-300
                                   text-white text-sm font-semibold rounded-lg transition-colors
                                   flex items-center gap-2 whitespace-nowrap flex-shrink-0">
                        {busy ? (
                          <><Loader2 size={14} className="animate-spin" />
                          {status === 'decomposing' ? 'Decomposing…' : 'Writing analysis…'}</>
                        ) : (
                          <><Sparkles size={14} /> Investigate with AI</>
                        )}
                      </button>
                    </div>

                    {status === 'idle' && !decomp && (
                      <p className="mt-4 pt-4 border-t border-slate-100 text-xs text-slate-400 leading-relaxed">
                        Click <strong className="text-slate-500">Investigate with AI</strong> to compare payment volume
                        during this window against the prior 30-day baseline across five business dimensions, then get
                        a plain-English root cause explanation from Claude.
                      </p>
                    )}
                  </div>

                  {errorMsg && (
                    <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl px-5 py-4 text-sm">
                      {errorMsg}
                    </div>
                  )}

                  {decomp && <SummaryCards summary={decomp.summary} />}
                  {decomp && <SegmentChart decomp={decomp} />}
                  {(status === 'narrating' || status === 'done') && (
                    <NarrativePanel narrative={narrative} streaming={status === 'narrating'} />
                  )}
                </>
              )}
            </main>
          </div>
        )}
      </div>

      <footer className="max-w-7xl mx-auto px-6 py-8 text-center text-xs text-slate-400">
        Detection: Prophet · Decomposition: custom SQL · Narrative: Claude (claude-opus-4-7) · Data: synthetic B2B payments 2022–2026
      </footer>
    </div>
  )
}
