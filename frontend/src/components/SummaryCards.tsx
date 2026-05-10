import type { Summary } from '@/lib/types'
import { TrendingDown, TrendingUp, DollarSign } from 'lucide-react'

function fmt(n: number) {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toFixed(0)}`
}

export default function SummaryCards({ summary }: { summary: Summary }) {
  const isSpike = summary.direction === 'spike'
  const pct = (summary.delta_pct * 100).toFixed(1)
  const sign = isSpike ? '+' : ''

  return (
    <div className="grid grid-cols-3 gap-4">
      {/* Baseline */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center gap-2 text-slate-500 text-sm font-medium mb-2">
          <DollarSign size={14} />
          Baseline avg / day
        </div>
        <div className="text-2xl font-bold text-slate-800">
          {fmt(summary.baseline_daily_tpv)}
        </div>
        <div className="text-xs text-slate-400 mt-1">30-day pre-anomaly window</div>
      </div>

      {/* Anomaly period */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center gap-2 text-slate-500 text-sm font-medium mb-2">
          <DollarSign size={14} />
          Anomaly period avg / day
        </div>
        <div className="text-2xl font-bold text-slate-800">
          {fmt(summary.anomaly_daily_tpv)}
        </div>
        <div className="text-xs text-slate-400 mt-1">During flagged window</div>
      </div>

      {/* Change */}
      <div className={`rounded-xl border p-5 shadow-sm ${
        isSpike ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'
      }`}>
        <div className={`flex items-center gap-2 text-sm font-medium mb-2 ${
          isSpike ? 'text-green-700' : 'text-red-700'
        }`}>
          {isSpike ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
          Change vs baseline
        </div>
        <div className={`text-2xl font-bold ${isSpike ? 'text-green-700' : 'text-red-700'}`}>
          {sign}{pct}%
        </div>
        <div className={`text-sm mt-1 font-medium ${isSpike ? 'text-green-600' : 'text-red-600'}`}>
          {sign}{fmt(summary.delta_daily_tpv)} / day
        </div>
      </div>
    </div>
  )
}
