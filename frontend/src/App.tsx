import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { clearTokens, getTokens, getUser } from './auth/api'
import { LoginPanel } from './auth/LoginPanel'
import { ChannelList } from './channels/ChannelList'
import { Conversation } from './messages/Conversation'
import { CopilotPanel } from './copilot/CopilotPanel'
import { colors, radii } from './theme'

/** Decode a JWT without verifying the signature — we only need the
 *  `sub` claim for the conversation view's `myUserId`. The server
 *  is the source of truth; this is a UI convenience. */
function decodeSub(jwt: string): string | null {
  try {
    const part = jwt.split('.')[1]
    if (!part) return null
    const padded = part.replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(padded + '==='.slice(0, (4 - (padded.length % 4)) % 4))
    const payload = JSON.parse(json) as { sub?: string }
    return payload.sub ?? null
  } catch {
    return null
  }
}

export default function App() {
  const { t, i18n } = useTranslation()
  const tokens = getTokens()
  const cachedUser = getUser()
  const [username, setUsername] = useState<string | null>(
    cachedUser?.rw_username ?? null,
  )
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null)
  const [myUserId, setMyUserId] = useState<string | null>(null)

  // Phase 5: a monotonically-increasing counter that, when bumped,
  // tells the ChannelList to refetch. We don't share the channel
  // array between App and ChannelList (ChannelList owns its state
  // for the create/leave flow); instead, bumping this counter is
  // a cheap way to re-trigger its effect.
  const [channelsVersion, setChannelsVersion] = useState(0)
  // Hold the bump function in a ref so the Conversation's effect
  // deps don't include it (the callback identity must be stable;
  // otherwise the auto-mark-read re-runs every time the version
  // changes).
  const bumpRef = useRef<() => void>(() => undefined)
  const bumpChannelsVersion = useCallback(() => {
    setChannelsVersion((v) => v + 1)
  }, [])
  bumpRef.current = bumpChannelsVersion
  const stableReadStateChanged = useCallback(() => {
    bumpRef.current()
  }, [])

  // Derive myUserId from the JWT `sub` whenever the access token changes.
  // The server is still the source of truth; this is just a UI convenience
  // so the conversation view can mark `is_mine` without a round-trip.
  useEffect(() => {
    if (!tokens) {
      setMyUserId(null)
      return
    }
    setMyUserId(decodeSub(tokens.access_token))
  }, [tokens])

  function onLogout() {
    clearTokens()
    setUsername(null)
    setSelectedChannelId(null)
    setMyUserId(null)
  }

  return (
    <div
      style={{
        fontFamily: 'system-ui, sans-serif',
        background: colors.background,
        color: colors.text,
        minHeight: '100vh',
        display: 'grid',
        gridTemplateRows: 'auto 1fr',
      }}
    >
      <header
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '0.6rem 1rem',
          background: colors.sidebar,
          borderBottom: `1px solid ${colors.border}`,
        }}
      >
        <h1 style={{ fontSize: '1rem', margin: 0, color: colors.textHeader }}>
          {t('app.title')}
        </h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            data-testid="lang-toggle"
            onClick={() => {
              const next = i18n.language === 'es' ? 'en' : 'es'
              i18n.changeLanguage(next)
              localStorage.setItem('rw_locale', next)
              // TODO: once PATCH /api/v1/me exists (issue #26), call
              // api.patchMe({ locale: next }) here to persist server-side.
            }}
            style={{
              padding: '0.3rem 0.6rem',
              background: 'transparent',
              color: colors.textMuted,
              border: `1px solid ${colors.border}`,
              borderRadius: radii.sm,
              cursor: 'pointer',
            }}
          >
            {i18n.language === 'es' ? t('app.lang.en') : t('app.lang.es')}
          </button>
          {username && (
            <button
              type="button"
              data-testid="logout-button"
              onClick={onLogout}
              style={{
                padding: '0.3rem 0.6rem',
                background: 'transparent',
                color: colors.textMuted,
                border: `1px solid ${colors.border}`,
                borderRadius: radii.sm,
                cursor: 'pointer',
              }}
            >
              {t('auth.logout')}
            </button>
          )}
        </div>
      </header>

      {tokens && username ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '280px 1fr 360px',
            minHeight: 0,
          }}
        >
          <ChannelList
            accessToken={tokens.access_token}
            username={username}
            selectedChannelId={selectedChannelId}
            onSelect={setSelectedChannelId}
            refreshTrigger={channelsVersion}
          />
          {selectedChannelId && myUserId ? (
            <Conversation
              accessToken={tokens.access_token}
              channelId={selectedChannelId}
              myUserId={myUserId}
              onReadStateChanged={stableReadStateChanged}
            />
          ) : (
            <div
              style={{
                display: 'grid',
                placeItems: 'center',
                background: colors.background,
                color: colors.textMuted,
              }}
            >
              {t('messages.select_channel')}
            </div>
          )}
          <CopilotPanel
            accessToken={tokens.access_token}
            contextKey={selectedChannelId ?? 'none'}
          />
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            placeItems: 'center',
            padding: '2rem',
          }}
        >
          <LoginPanel onAuthenticated={(u) => setUsername(u)} />
        </div>
      )}
    </div>
  )
}
