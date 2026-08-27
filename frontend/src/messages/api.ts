export interface Message {
  rw_id: string
  rw_channel_id: string
  rw_author_id: string
  rw_body: string
  rw_is_edited: boolean
  rw_created_at: string
  rw_edited_at: string | null
  is_mine: boolean
}

export interface HistoryPage {
  items: Message[]
  next_cursor_created_at: string | null
  next_cursor_id: string | null
}

// Phase 5: a search hit carries the original body + the
// ts_headline-wrapped highlight (the `<mark>` tags wrap the
// matching tokens). The frontend renders the highlight directly
// via dangerouslySetInnerHTML — the only HTML tag emitted is
// `<mark>`, so a sanitiser pass is not needed in production.
// `<mark>` is also a benign tag that React's textContent escape
// would strip otherwise.
export interface SearchHit {
  rw_id: string
  rw_channel_id: string
  rw_author_id: string
  rw_body: string
  rw_created_at: string
  rw_highlight: string
  is_mine: boolean
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export async function fetchHistory(
  accessToken: string,
  channelId: string,
  cursor: { ts: string; id: string } | null = null,
  limit = 50,
): Promise<HistoryPage> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) {
    params.set('cursor_ts', cursor.ts)
    params.set('cursor_id', cursor.id)
  }
  const resp = await fetch(
    `${BASE_URL}/api/v1/channels/${channelId}/messages?${params}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  )
  if (!resp.ok) throw new Error('history_failed')
  return (await resp.json()) as HistoryPage
}

export interface SendResult {
  message: Message
  wasReplay: boolean
}

export async function sendMessage(
  accessToken: string,
  channelId: string,
  body: string,
  clientRef: string,
): Promise<SendResult> {
  const resp = await fetch(
    `${BASE_URL}/api/v1/channels/${channelId}/messages`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ body, client_ref: clientRef }),
    },
  )
  if (!resp.ok && resp.status !== 200) throw new Error('send_failed')
  // 200 = idempotent replay; 201 = fresh insert. Both have the
  // same body shape; the X-Idempotent-Replay header tells us which.
  const wasReplay = resp.headers.get('X-Idempotent-Replay') === 'true'
  return {
    message: (await resp.json()) as Message,
    wasReplay,
  }
}

export async function editMessage(
  accessToken: string,
  messageId: string,
  body: string,
): Promise<Message> {
  const resp = await fetch(
    `${BASE_URL}/api/v1/messages/${messageId}`,
    {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ body }),
    },
  )
  if (!resp.ok) throw new Error('edit_failed')
  return (await resp.json()) as Message
}

export async function deleteMessage(
  accessToken: string,
  messageId: string,
  reason = 'user-deleted',
): Promise<void> {
  const resp = await fetch(
    `${BASE_URL}/api/v1/messages/${messageId}/delete`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ reason }),
    },
  )
  if (!resp.ok) throw new Error('delete_failed')
}

// ─── Phase 5: search + bulk mark-read ────────────────────────────────────


export async function searchInChannel(
  accessToken: string,
  channelId: string,
  query: string,
  limit = 20,
): Promise<SearchHit[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  const resp = await fetch(
    `${BASE_URL}/api/v1/channels/${channelId}/search?${params}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  )
  if (!resp.ok) throw new Error('search_failed')
  const body = (await resp.json()) as { items: SearchHit[] }
  return body.items
}


export async function markChannelRead(
  accessToken: string,
  channelId: string,
): Promise<{ inserted: number }> {
  const resp = await fetch(
    `${BASE_URL}/api/v1/channels/${channelId}/read`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  )
  if (!resp.ok) throw new Error('mark_channel_failed')
  return (await resp.json()) as { inserted: number }
}
