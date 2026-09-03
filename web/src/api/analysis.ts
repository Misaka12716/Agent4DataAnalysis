import type { SsePayload } from '@/types/api'

export interface AnalysisStreamOptions {
  sessionId: string
  inputData: string
  signal?: AbortSignal
  onEvent: (event: SsePayload) => void
}

export async function runAnalysisStream(options: AnalysisStreamOptions): Promise<void> {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  const response = await fetch(`${base}/run-analysis`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: options.sessionId,
      input_data: options.inputData,
    }),
    signal: options.signal,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const errBody = await response.json()
      detail = errBody.detail || errBody.msg || detail
    } catch {
      /* ignore */
    }
    throw new Error(detail || `分析请求失败 (${response.status})`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('无法读取流式响应')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data:')) continue
      const jsonStr = trimmed.slice(5).trim()
      if (!jsonStr) continue
      try {
        const payload = JSON.parse(jsonStr) as SsePayload
        options.onEvent(payload)
      } catch {
        /* skip malformed lines */
      }
    }
  }
}
