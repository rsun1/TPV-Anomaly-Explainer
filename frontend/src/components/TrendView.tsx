'use client'

import { useEffect, useState } from 'react'
import { Loader2, ChevronDown } from 'lucide-react'
import TrendChart from './TrendChart'
import { PRODUCT_COLORS, PRODUCT_LABELS, ALL_PRODUCTS } from '@/lib/constants'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface DataPoint    { date: string; tpv: number }
interface AnomalyWindow { start: string; end: string }
interface TimeseriesResponse {
  product: string
  series: DataPoint[]
  anomaly_windows: AnomalyWindow[]
}

export default function TrendView() {
  const [product, setProduct]   = useState('regular_ach')
  const [data, setData]         = useState<TimeseriesResponse | null>(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    fetch(`${API}/timeseries/${product}`)
      .then(r => {
        if (!r.ok) throw new Error(`Backend returned ${r.status}`)
        return r.json()
      })
      .then(d => setData(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [product])

  return (
    <div className="space-y-5">
      {/* Product selector */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5">Product</label>
            <div className="relative">
              <select
                value={product}
                onChange={e => setProduct(e.target.value)}
                disabled={loading}
                className="appearance-none bg-slate-50 border border-slate-200 rounded-lg
                           px-3 py-2.5 pr-8 text-sm text-slate-800 font-medium
                           focus:outline-none focus:ring-2 focus:ring-violet-500 disabled:opacity-50"
              >
                {ALL_PRODUCTS.map(p => (
                  <option key={p} value={p}>{PRODUCT_LABELS[p]}</option>
                ))}
              </select>
              <ChevronDown size={14} className="absolute right-2.5 top-3 text-slate-400 pointer-events-none" />
            </div>
          </div>

          {/* Product color swatch + legend */}
          <div className="flex gap-3 mt-5">
            {ALL_PRODUCTS.map(p => (
              <button
                key={p}
                onClick={() => setProduct(p)}
                className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full border transition-colors ${
                  p === product
                    ? 'border-slate-300 bg-slate-100 font-semibold text-slate-700'
                    : 'border-transparent text-slate-500 hover:bg-slate-50'
                }`}
              >
                <span
                  className="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0"
                  style={{ backgroundColor: PRODUCT_COLORS[p] }}
                />
                {PRODUCT_LABELS[p]}
              </button>
            ))}
          </div>
        </div>

        <p className="mt-4 text-xs text-slate-400 leading-relaxed">
          Shaded red regions mark windows where the detection model flagged an anomaly.
          Switch to the <strong className="text-slate-500">Explainer</strong> tab to see Claude&apos;s root cause analysis for any event.
        </p>
      </div>

      {/* Chart */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <div className="flex items-baseline gap-2 mb-1">
          <h2 className="text-base font-semibold text-slate-800">
            Daily TPV — {PRODUCT_LABELS[product]}
          </h2>
          <span className="text-xs text-slate-400">complete rows only, Jan 2022 – May 2026</span>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-16 justify-center">
            <Loader2 size={16} className="animate-spin" />
            Loading time series...
          </div>
        )}

        {error && (
          <div className="text-red-600 text-sm py-6">{error}</div>
        )}

        {!loading && !error && data && (
          <>
            <TrendChart
              product={product}
              series={data.series}
              anomalyWindows={data.anomaly_windows}
            />

            {/* Anomaly legend */}
            {data.anomaly_windows.length > 0 && (
              <div className="mt-4 pt-4 border-t border-slate-100 flex flex-wrap gap-x-6 gap-y-1">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span className="inline-block w-3 h-3 rounded-sm bg-red-400 opacity-50" />
                  Anomaly window ({data.anomaly_windows.length} event{data.anomaly_windows.length > 1 ? 's' : ''} flagged)
                </div>
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  Hover over the chart to see daily values
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
