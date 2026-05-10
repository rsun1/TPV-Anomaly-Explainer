'use client'

import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceArea, ResponsiveContainer,
} from 'recharts'
import { PRODUCT_COLORS } from '@/lib/constants'

interface DataPoint {
  date: string
  tpv: number
}
interface AnomalyWindow {
  start: string
  end: string
}
interface Props {
  product: string
  series: DataPoint[]
  anomalyWindows: AnomalyWindow[]
}

// Generate quarterly x-axis ticks from the series date range
function buildTicks(series: DataPoint[]): string[] {
  if (!series.length) return []
  const first = new Date(series[0].date)
  const last  = new Date(series[series.length - 1].date)
  const ticks: string[] = []
  const d = new Date(first.getFullYear(), Math.floor(first.getMonth() / 3) * 3, 1)
  while (d <= last) {
    ticks.push(d.toISOString().slice(0, 10))
    d.setMonth(d.getMonth() + 3)
  }
  return ticks
}

function fmtTick(dateStr: string) {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
}

function fmtTPV(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(0)}K`
  return `$${v}`
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  const d = new Date(label + 'T00:00:00')
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-lg px-3 py-2 text-xs">
      <p className="text-slate-500 mb-0.5">
        {d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}
      </p>
      <p className="font-semibold text-slate-800">{fmtTPV(payload[0].value)}</p>
    </div>
  )
}

export default function TrendChart({ product, series, anomalyWindows }: Props) {
  const color = PRODUCT_COLORS[product] ?? '#6366f1'
  const ticks = buildTicks(series)

  return (
    <ResponsiveContainer width="100%" height={380}>
      <AreaChart data={series} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          <linearGradient id="tpvGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor={color} stopOpacity={0.15} />
            <stop offset="95%" stopColor={color} stopOpacity={0.01} />
          </linearGradient>
        </defs>

        <CartesianGrid vertical={false} stroke="#f1f5f9" />

        <XAxis
          dataKey="date"
          ticks={ticks}
          tickFormatter={fmtTick}
          tick={{ fontSize: 10, fill: '#94a3b8' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tickFormatter={fmtTPV}
          tick={{ fontSize: 10, fill: '#94a3b8' }}
          axisLine={false}
          tickLine={false}
          width={52}
        />

        <Tooltip content={<CustomTooltip />} />

        {/* Anomaly bands — no labels, just visual markers */}
        {anomalyWindows.map((w, i) => (
          <ReferenceArea
            key={i}
            x1={w.start}
            x2={w.end}
            fill="#ef4444"
            fillOpacity={0.12}
            stroke="#ef4444"
            strokeOpacity={0.3}
            strokeWidth={1}
          />
        ))}

        <Area
          type="monotone"
          dataKey="tpv"
          stroke={color}
          strokeWidth={1.5}
          fill="url(#tpvGradient)"
          dot={false}
          activeDot={{ r: 3, fill: color, stroke: '#fff', strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
