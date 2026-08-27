export interface Channel {
  channel_id: string
  name: string
  kind: 1 | 2 // 1 = direct, 2 = group
  created_by: string
  created_at: string
  my_role: 1 | 2 // 1 = member, 2 = owner
}

export async function listChannels(accessToken: string): Promise<Channel[]> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  const resp = await fetch(`${baseUrl}/api/v1/channels`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!resp.ok) throw new Error('list_failed')
  const body = (await resp.json()) as { items: Channel[] }
  return body.items
}

export async function createGroupChannel(
  accessToken: string,
  name: string,
): Promise<Channel> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  const resp = await fetch(`${baseUrl}/api/v1/channels/group`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ name }),
  })
  if (!resp.ok) throw new Error('create_failed')
  return (await resp.json()) as Channel
}

export async function createDirectChannel(
  accessToken: string,
  otherUsername: string,
): Promise<Channel> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  const resp = await fetch(`${baseUrl}/api/v1/channels/direct`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ other_username: otherUsername }),
  })
  if (!resp.ok) throw new Error('create_failed')
  return (await resp.json()) as Channel
}

export async function leaveChannel(
  accessToken: string,
  channelId: string,
): Promise<void> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  const resp = await fetch(`${baseUrl}/api/v1/channels/${channelId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!resp.ok) throw new Error('leave_failed')
}
