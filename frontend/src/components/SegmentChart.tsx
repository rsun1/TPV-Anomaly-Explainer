'use client'

import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, ResponsiveContainer,
} from 'recharts'
import type { Decomposition, ChartSegment } from '@/lib/types'

function buildChartData(decomp: Decomposition): ChartSegment[] {
  const rows: ChartSegment[] = []

  // Flatten all dimensions — top 3 segments per dimension
  for (const [dim, segs] of Object.entries(decomp.by_dimension)) {
    for (const seg of segs.slice(0, 3)) {
      rows.push({
        label: `${dim.replace(/_/g, ' ')}: ${seg.segment}`,
        dimension: dim,
        delta: seg.delta_daily_tpv,
        direction: seg.direction,
        pct: seg.pct_of_total_delta * 100,
      })
    }
  }

  // Sort by absolute delta, keep top 12
  return rows
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 12)
}

function fmtDollar(val: number) {
  const abs = Math.abs(val)
  const sign = val < 0 ? '-' : '+'
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`
  return `${sign}$${abs.toFixed(0)}`
}

const CustomTooltip = ({ active, payload }: any) => {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as ChartSegment
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-sm">
      <p className="font-semibold text-slate-800 mb-1">{d.label}</p>
      <p className={d.direction === 'drop' ? 'text-red-600' : 'text-green-600'}>
        {fmtDollar(d.delta)} / day
      </p>
      <p className="text-slate-500">{d.pct.toFixed(1)}% of total change</p>
    </div>
  )
}

export default function SegmentChart({ decomp }: { decomp: Decomposition }) {
  const data = buildChartData(decomp)

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
      <h2 className="text-base font-semibold text-slate-800 mb-1">
        Top Contributing Segments
      </h2>
      <p className="text-sm text-slate-500 mb-5">
        Which customer or merchant segments drove the most change? Ranked by daily TPV delta.
      </p>

      <ResponsiveContainer width="100%" height={data.length * 38 + 40}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 60, left: 8, bottom: 0 }}
          barCategoryGap="30%"
        >
          <CartesianGrid horizontal={false} stroke="#f1f5f9" />
          <XAxis
            type="number"
            tickFormatter={fmtDollar}
            tick={{ fontSize: 11, fill: '#64748b' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={220}
            tick={{ fontSize: 11, fill: '#475569' }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: '#f8fafc' }} />
          <Bar dataKey="delta" radius={[0, 4, 4, 0]}>
            {data.map((entry, i) => (
              <Cell
                key={i}
                fill={
                  entry.direction === 'drop'
                    ? '#ef4444'
                    : entry.direction === 'spike'
                    ? '#22c55e'
                    : '#94a3b8'
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      <div className="flex gap-5 mt-4 text-xs text-slate-500">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-red-500" /> Drop
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-green-500" /> Spike
        </span>
      </div>
    </div>
  )
}
