'use client'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Sparkles, Loader2 } from 'lucide-react'

interface Props {
  narrative: string
  streaming: boolean
}

export default function NarrativePanel({ narrative, streaming }: Props) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
      <div className="flex items-center gap-2 mb-4">
        <Sparkles size={16} className="text-violet-500" />
        <h2 className="text-base font-semibold text-slate-800">Root Cause Analysis</h2>
        <span className="ml-auto text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
          claude-opus-4-7
        </span>
      </div>

      {streaming && narrative.length === 0 && (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-6">
          <Loader2 size={14} className="animate-spin" />
          Claude is analyzing the decomposition...
        </div>
      )}

      {narrative.length > 0 && (
        <div className="narrative-content text-sm text-slate-700 leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{narrative}</ReactMarkdown>
        </div>
      )}

      {streaming && narrative.length > 0 && (
        <span className="inline-block w-1.5 h-4 bg-violet-400 ml-0.5 animate-pulse rounded-sm" />
      )}
    </div>
  )
}
