import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type Channel,
  createDirectChannel,
  createGroupChannel,
  leaveChannel,
  listChannels,
} from './api'

interface Props {
  accessToken: string
  username: string
  selectedChannelId: string | null
  onSelect: (channelId: string) => void
}

export function ChannelList({
  accessToken,
  username,
  selectedChannelId,
  onSelect,
}: Props) {
  const { t } = useTranslation()
  const [channels, setChannels] = useState<Channel[]>([])
  const [busy, setBusy] = useState(false)
  const [showForm, setShowForm] = useState<'group' | 'direct' | null>(null)
  const [groupName, setGroupName] = useState('')
  const [directUsername, setDirectUsername] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    try {
      const items = await listChannels(accessToken)
      setChannels(items)
    } catch (err) {
      setError(String(err))
    }
  }

  useEffect(() => {
    void refresh()
  }, [accessToken])

  async function onCreateGroup(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createGroupChannel(accessToken, groupName)
      setGroupName('')
      setShowForm(null)
      await refresh()
      onSelect(created.channel_id)
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  async function onCreateDirect(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await createDirectChannel(accessToken, directUsername)
      setDirectUsername('')
      setShowForm(null)
      await refresh()
      onSelect(created.channel_id)
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  async function onLeave(channelId: string) {
    setError(null)
    try {
      await leaveChannel(accessToken, channelId)
      if (selectedChannelId === channelId) {
        onSelect('')
      }
      await refresh()
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <aside
      style={{
        display: 'grid',
        gridTemplateRows: 'auto 1fr auto',
        background: '#2F3136',
        color: '#DCDDDE',
        borderRight: '1px solid #202225',
        minHeight: 0,
      }}
    >
      <header
        style={{
          padding: '0.75rem 1rem',
          borderBottom: '1px solid #202225',
          fontWeight: 600,
        }}
      >
        {t('channels.title')}
      </header>
      <p
        style={{
          padding: '0.4rem 1rem',
          color: '#72767D',
          fontSize: '0.85em',
          margin: 0,
          borderBottom: '1px solid #202225',
        }}
      >
        {t('auth.logged_in_as', { name: username })}
      </p>

      <ul
        data-testid="channel-list"
        style={{
          listStyle: 'none',
          padding: '0.4rem 0.5rem',
          margin: 0,
          overflowY: 'auto',
          display: 'grid',
          gap: 2,
        }}
      >
        {channels.length === 0 && (
          <li style={{ padding: '0.5rem', color: '#72767D' }}>
            {t('channels.empty')}
          </li>
        )}
        {channels.map((c) => {
          const active = c.channel_id === selectedChannelId
          return (
            <li
              key={c.channel_id}
              data-testid={`channel-${c.name}`}
              data-channel-id={c.channel_id}
            >
              <button
                type="button"
                onClick={() => onSelect(c.channel_id)}
                data-active={active ? 'true' : 'false'}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '0.4rem 0.6rem',
                  background: active ? '#40444B' : 'transparent',
                  color: active ? '#FFFFFF' : '#DCDDDE',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                <span>
                  {c.name}{' '}
                  <small style={{ color: '#72767D' }}>
                    {c.my_role === 2
                      ? t('channels.owner_badge')
                      : t('channels.member_badge')}
                  </small>
                </span>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation()
                    void onLeave(c.channel_id)
                  }}
                  style={{ fontSize: '0.8em', color: '#72767D', padding: '0 4px' }}
                >
                  ✕
                </span>
              </button>
            </li>
          )
        })}
      </ul>

      <div
        style={{
          padding: '0.5rem 1rem',
          borderTop: '1px solid #202225',
          display: 'grid',
          gap: 4,
        }}
      >
        <div style={{ display: 'flex', gap: 4 }}>
          <button
            type="button"
            data-testid="new-group-button"
            onClick={() => setShowForm('group')}
            style={{
              flex: 1,
              padding: '0.4rem 0.6rem',
              background: '#5865F2',
              color: '#FFFFFF',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            {t('channels.new_group')}
          </button>
          <button
            type="button"
            data-testid="new-direct-button"
            onClick={() => setShowForm('direct')}
            style={{
              flex: 1,
              padding: '0.4rem 0.6rem',
              background: '#5865F2',
              color: '#FFFFFF',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            {t('channels.new_direct')}
          </button>
        </div>

        {showForm === 'group' && (
          <form
            onSubmit={onCreateGroup}
            data-testid="new-group-form"
            style={{ display: 'grid', gap: 4 }}
          >
            <input
              data-testid="new-group-name"
              placeholder={t('channels.name') ?? ''}
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              required
              minLength={1}
              maxLength={120}
              style={{
                padding: '0.4rem 0.6rem',
                background: '#40444B',
                color: '#DCDDDE',
                border: 'none',
                borderRadius: 4,
              }}
            />
            <div style={{ display: 'flex', gap: 4 }}>
              <button
                type="submit"
                data-testid="create-group-submit"
                disabled={busy}
                style={{
                  flex: 1,
                  padding: '0.4rem',
                  background: '#3BA55D',
                  color: '#FFFFFF',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                {t('channels.create')}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(null)}
                style={{
                  flex: 1,
                  padding: '0.4rem',
                  background: 'transparent',
                  color: '#72767D',
                  border: '1px solid #202225',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                {t('channels.cancel')}
              </button>
            </div>
          </form>
        )}
        {showForm === 'direct' && (
          <form
            onSubmit={onCreateDirect}
            data-testid="new-direct-form"
            style={{ display: 'grid', gap: 4 }}
          >
            <input
              data-testid="new-direct-username"
              placeholder={t('channels.other_username') ?? ''}
              value={directUsername}
              onChange={(e) => setDirectUsername(e.target.value)}
              required
              minLength={1}
              maxLength={64}
              style={{
                padding: '0.4rem 0.6rem',
                background: '#40444B',
                color: '#DCDDDE',
                border: 'none',
                borderRadius: 4,
              }}
            />
            <div style={{ display: 'flex', gap: 4 }}>
              <button
                type="submit"
                disabled={busy}
                style={{
                  flex: 1,
                  padding: '0.4rem',
                  background: '#3BA55D',
                  color: '#FFFFFF',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                {t('channels.create')}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(null)}
                style={{
                  flex: 1,
                  padding: '0.4rem',
                  background: 'transparent',
                  color: '#72767D',
                  border: '1px solid #202225',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                {t('channels.cancel')}
              </button>
            </div>
          </form>
        )}

        {error && (
          <p style={{ color: '#ED4245', fontSize: '0.85em' }}>{error}</p>
        )}
      </div>
    </aside>
  )
}
