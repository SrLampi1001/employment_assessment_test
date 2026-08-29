export interface TokenPair {
  access_token: string
  refresh_token: string
  refresh_expires_at: string
}

export interface AuthUser {
  rw_id: string
  rw_username: string
  rw_display_name: string
  rw_locale: 'es' | 'en'
}

const TOKEN_KEY = 'rw_tokens'
const USER_KEY = 'rw_user'

export function getTokens(): TokenPair | null {
  const raw = localStorage.getItem(TOKEN_KEY)
  return raw ? (JSON.parse(raw) as TokenPair) : null
}

export function setTokens(t: TokenPair): void {
  localStorage.setItem(TOKEN_KEY, JSON.stringify(t))
}

export function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function getUser(): AuthUser | null {
  const raw = localStorage.getItem(USER_KEY)
  return raw ? (JSON.parse(raw) as AuthUser) : null
}

export function setUser(u: AuthUser): void {
  localStorage.setItem(USER_KEY, JSON.stringify(u))
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export async function login(
  username: string,
  password: string,
): Promise<TokenPair> {
  const resp = await fetch(`${BASE_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!resp.ok) {
    throw new Error('invalid_credentials')
  }
  const pair = (await resp.json()) as TokenPair
  setTokens(pair)
  return pair
}

export async function register(
  username: string,
  password: string,
  locale: 'es' | 'en',
): Promise<void> {
  const resp = await fetch(`${BASE_URL}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username,
      display_name: username,
      locale,
      password,
    }),
  })
  if (!resp.ok) {
    throw new Error('register_failed')
  }
}

export async function me(): Promise<AuthUser> {
  const tokens = getTokens()
  if (!tokens) throw new Error('not_authenticated')
  const resp = await fetch(`${BASE_URL}/api/v1/me`, {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  })
  if (!resp.ok) throw new Error('unauthorized')
  const body = (await resp.json()) as { actor_id: string }
  // Phase 3 doesn't yet return a full profile; we resolve via the
  // access JWT (server-stamped) by introspecting it. For Phase 3 we
  // just store what the search endpoint returns for the actor's
  // username later. Simpler: keep user state on the client.
  const cached = getUser()
  return cached ?? {
    rw_id: body.actor_id,
    // TODO: remove fallback once PATCH /api/v1/me returns a full
    // AuthUser (issue #26 on GitHub). Currently rw_username and
    // rw_display_name are not surfaced anywhere in the UI so the
    // fallback values are never visible.
    rw_username: 'me',
    rw_display_name: 'me',
    // Intentionally 'es' — the language toggle persists to localStorage
    // via App.tsx onToggle; this fallback is only hit on a completely
    // fresh browser with no localStorage.
    rw_locale: 'es',
  }
}
