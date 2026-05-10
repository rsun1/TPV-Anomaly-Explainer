'use client'

import { useEffect, useState } from 'react'
import { Activity, ChevronDown, Loader2, TrendingUp, Sparkles } from 'lucide-react'
import SummaryCards from '@/components/SummaryCards'
import SegmentChart from '@/components/SegmentChart'
import NarrativePanel from '@/components/NarrativePanel'
import TrendView from '@/components/TrendView'
import { streamAnalysis } from '@/lib/stream'
import { PRODUCT_LABELS } from '@/lib/constants'
import type { AnomalyEvent, Decomposition } from '@/lib/types'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

type Tab    = 'trend' | 'explain'
type Status = 'idle' | 'decomposing' | 'narrating' | 'done' | 'error'

export default function Page() {
  const [tab, setTab]             = useState<Tab>('trend')
  const [events, setEvents]       = useState<AnomalyEvent[]>([])
  const [eventIdx, setEventIdx]   = useState(0)
  const [product, setProduct]     = useState('')
  const [status, setStatus]       = useState<Status>('idle')
  const [decomp, setDecomp]       = useState<Decomposition | null>(null)
  const [narrative, setNarrative] = useState('')
  const [errorMsg, setErrorMsg]   = useState('')

  useEffect(() => {
    fetch(`${API}/events`)
      .then(r => r.json())
      .then((data: AnomalyEvent[]) => {
        setEvents(data)
        setProduct(data[0]?.products[0] ?? '')
      })
      .catch(() => setErrorMsg('Could not reach the backend. Is it running on port 8000?'))
  }, [])

  const selectedEvent = events[eventIdx]

  function handleEventChange(idx: number) {
    setEventIdx(idx)
    setProduct(events[idx].products[0])
    setDecomp(null)
    setNarrative('')
    setStatus('idle')
  }

  async function handleAnalyze() {
    if (!selectedEvent || !product) return
    setStatus('decomposing')
    setDecomp(null)
    setNarrative('')
    setErrorMsg('')

    await streamAnalysis(product, selectedEvent.start, selectedEvent.end, {
      onDecomposition(data) {
        setDecomp(data)
        setStatus('narrating')
      },
      onChunk(text) {
        setNarrative(prev => prev + text)
      },
      onDone() {
        setStatus('done')
      },
      onError(msg) {
        setErrorMsg(msg)
        setStatus('error')
      },
    })
  }

  const busy = status === 'decomposing' || status === 'narrating'

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center gap-3">
          <div className="bg-violet-600 p-1.5 rounded-lg">
            <Activity size={18} className="text-white" />
          </div>
          <div className="flex-1">
            <h1 className="text-base font-bold text-slate-900 leading-none">
              Anomaly Explainer
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              AI-powered payment TPV root cause analysis
            </p>
          </div>

          {/* Tab nav */}
          <nav className="flex gap-1 bg-slate-100 rounded-lg p-1">
            <button
              onClick={() => setTab('trend')}
              className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === 'trend'
                  ? 'bg-white text-slate-800 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <TrendingUp size={14} />
              Trend
            </button>
            <button
              onClick={() => setTab('explain')}
              className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === 'explain'
                  ? 'bg-white text-slate-800 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Sparkles size={14} />
              Explainer
            </button>
          </nav>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">

        {/* ── Trend tab ── */}
        {tab === 'trend' && <TrendView />}

        {/* ── Explainer tab ── */}
        {tab === 'explain' && (
          <>
            {/* Control Panel */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <h2 className="text-sm font-semibold text-slate-700 mb-4">Select anomaly event</h2>

              <div className="flex flex-wrap gap-4 items-end">
                {/* Event picker */}
                <div className="flex-1 min-w-56">
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">
                    Anomaly window
                  </label>
                  <div className="relative">
                    <select
                      value={eventIdx}
                      onChange={e => handleEventChange(Number(e.target.value))}
                      disabled={busy}
                      className="w-full appearance-none bg-slate-50 border border-slate-200 rounded-lg
                                 px-3 py-2.5 text-sm text-slate-800 font-medium pr-8
                                 focus:outline-none focus:ring-2 focus:ring-violet-500 disabled:opacity-50"
                    >
                      {events.map((ev, i) => (
                        <option key={ev.id} value={i}>
                          Event {ev.id} — {ev.label}
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={14} className="absolute right-2.5 top-3 text-slate-400 pointer-events-none" />
                  </div>
                </div>

                {/* Product picker */}
                {selectedEvent && selectedEvent.products.length > 1 && (
                  <div className="w-48">
                    <label className="block text-xs font-medium text-slate-500 mb-1.5">
                      Product
                    </label>
                    <div className="relative">
                      <select
                        value={product}
                        onChange={e => setProduct(e.target.value)}
                        disabled={busy}
                        className="w-full appearance-none bg-slate-50 border border-slate-200 rounded-lg
                                   px-3 py-2.5 text-sm text-slate-800 font-medium pr-8
                                   focus:outline-none focus:ring-2 focus:ring-violet-500 disabled:opacity-50"
                      >
                        {selectedEvent.products.map(p => (
                          <option key={p} value={p}>{PRODUCT_LABELS[p] ?? p}</option>
                        ))}
                      </select>
                      <ChevronDown size={14} className="absolute right-2.5 top-3 text-slate-400 pointer-events-none" />
                    </div>
                  </div>
                )}

                {/* Analyze button */}
                <button
                  onClick={handleAnalyze}
                  disabled={busy || events.length === 0}
                  className="px-6 py-2.5 bg-violet-600 hover:bg-violet-700 disabled:bg-violet-300
                             text-white text-sm font-semibold rounded-lg transition-colors
                             flex items-center gap-2 whitespace-nowrap"
                >
                  {busy ? (
                    <>
                      <Loader2 size={14} className="animate-spin" />
                      {status === 'decomposing' ? 'Decomposing...' : 'Generating narrative...'}
                    </>
                  ) : 'Analyze'}
                </button>
              </div>

              <div className="mt-4 pt-4 border-t border-slate-100 text-xs text-slate-500 leading-relaxed">
                <strong className="text-slate-600">What happens when you click Analyze:</strong>{' '}
                The system compares payment volume during this window against the prior 30-day
                baseline across five business dimensions (merchant type, size, payer type, size,
                and customer tenure). It then sends those findings to Claude, which reasons through
                the data and produces a ranked explanation of the most likely root cause — in plain
                English, in about 30 seconds.
              </div>
            </div>

            {/* Error */}
            {errorMsg && (
              <div className="bg-red-50 border border-red-200 text-red-700 rounded-xl px-5 py-4 text-sm">
                {errorMsg}
              </div>
            )}

            {/* Results */}
            {decomp && (
              <>
                <SummaryCards summary={decomp.summary} />
                <SegmentChart decomp={decomp} />
              </>
            )}

            {(status === 'narrating' || status === 'done') && (
              <NarrativePanel narrative={narrative} streaming={status === 'narrating'} />
            )}
          </>
        )}

      </main>

      <footer className="max-w-6xl mx-auto px-6 py-8 text-center text-xs text-slate-400">
        Detection: Prophet &nbsp;·&nbsp; Decomposition: custom SQL &nbsp;·&nbsp;
        Narrative: Claude (claude-opus-4-7) &nbsp;·&nbsp;
        Data: synthetic B2B payments 2022–2026
      </footer>
    </div>
  )
}
