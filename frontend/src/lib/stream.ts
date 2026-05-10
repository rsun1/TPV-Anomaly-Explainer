import type { Decomposition } from './types'

interface StreamCallbacks {
  onDecomposition: (data: Decomposition) => void
  onChunk: (text: string) => void
  onDone: () => void
  onError: (msg: string) => void
}

// Next.js rewrites buffer the full response before forwarding — unusable for SSE.
// Call FastAPI directly from the browser instead (CORS is configured on the backend).
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

/**
 * POST directly to FastAPI /analyze and reads the SSE stream.
 * Calls the appropriate callback for each event type.
 */
export async function streamAnalysis(
  product: string,
  start: string,
  end: string,
  callbacks: StreamCallbacks,
): Promise<void> {
  const res = await fetch(`${API}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product, start, end }),
  })

  if (!res.ok) {
    const text = await res.text()
    callbacks.onError(`Backend error ${res.status}: ${text}`)
    return
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // SSE messages are separated by double newlines
    const messages = buffer.split('\n\n')
    buffer = messages.pop() ?? ''

    for (const msg of messages) {
      const line = msg.trim()
      if (!line.startsWith('data: ')) continue
      const json = JSON.parse(line.slice(6))

      if (json.type === 'decomposition') callbacks.onDecomposition(json.data)
      else if (json.type === 'chunk') callbacks.onChunk(json.text)
      else if (json.type === 'done') callbacks.onDone()
    }
  }
}
