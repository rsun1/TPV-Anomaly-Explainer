export interface AnomalyEvent {
  id: number
  label: string
  start: string
  end: string
  products: string[]
}

export interface SegmentRow {
  segment: string
  baseline_daily_tpv: number
  anomaly_daily_tpv: number
  delta_daily_tpv: number
  pct_of_total_delta: number
  direction: 'spike' | 'drop' | 'flat'
}

export interface Summary {
  baseline_daily_tpv: number
  anomaly_daily_tpv: number
  delta_daily_tpv: number
  delta_pct: number
  direction: 'spike' | 'drop'
}

export interface Decomposition {
  product: string
  anomaly_window: { start: string; end: string; days: number }
  baseline_window: { start: string; end: string; days: number }
  summary: Summary
  by_dimension: Record<string, SegmentRow[]>
  top_interactions: (SegmentRow & { dimensions: string })[]
}

// Flattened segment for the chart
export interface ChartSegment {
  label: string        // "merchant_industry: ecommerce"
  dimension: string
  delta: number
  direction: 'spike' | 'drop' | 'flat'
  pct: number
}
