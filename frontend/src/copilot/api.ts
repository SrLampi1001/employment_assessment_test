export interface Citation {
  rw_id: string
  rw_channel_id: string
  snippet: string
}

export type DenialCode =
  | 'deny:no-permission'
  | 'deny:out-of-scope'
  | 'deny:insufficient-context'
  | 'infer:low-confidence'
  | null

export interface CopilotAnswer {
  text: string
  citations: Citation[]
  /** One of the four taxonomy codes (see references/denial-taxonomy.md)
   *  when the model refused / flagged; null for a normal answer. */
  denial_code: DenialCode
  /** "high" for a normal answer; "low" for any of the four denial /
   *  inference codes (the answer should be read with the right
   *  amount of skepticism). */
  confidence: 'low' | 'high'
  /** PROMPT_VERSION from the backend; lets the frontend warn when
   *  a prompt upgrade is pending review. */
  prompt_version: string
}

export interface CopilotUsage {
  total_calls: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_cost_usd: number
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export async function askCopilot(
  accessToken: string,
  question: string,
  topK = 5,
): Promise<CopilotAnswer> {
  const resp = await fetch(`${BASE_URL}/api/v1/copilot/query`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ question, top_k: topK }),
  })
  if (!resp.ok) throw new Error('copilot_failed')
  return (await resp.json()) as CopilotAnswer
}

export async function fetchCopilotUsage(
  accessToken: string,
): Promise<CopilotUsage> {
  const resp = await fetch(`${BASE_URL}/api/v1/copilot/usage`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!resp.ok) throw new Error('usage_failed')
  return (await resp.json()) as CopilotUsage
}