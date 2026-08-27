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
}

export function ChannelList({ accessToken, username }: Props) {
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
      await createGroupChannel(accessToken, groupName)
      setGroupName('')
      setShowForm(null)
      await refresh()
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
      await createDirectChannel(accessToken, directUsername)
      setDirectUsername('')
      setShowForm(null)
      await refresh()
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
      await refresh()
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <aside style={{ display: 'grid', gap: '0.75rem', maxWidth: 320 }}>
      <h2>{t('channels.title')}</h2>
      <p style={{ color: '#666', fontSize: '0.85em' }}>
        {t('auth.logged_in_as', { name: username })}
      </p>
      {channels.length === 0 && <p>{t('channels.empty')}</p>}
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 4 }}>
        {channels.map((c) => (
          <li
            key={c.channel_id}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '0.4rem 0.6rem',
              border: '1px solid #ddd',
              borderRadius: 4,
            }}
          >
            <span>
              {c.name}{' '}
              <small style={{ color: '#888' }}>
                {c.my_role === 2
                  ? t('channels.owner_badge')
                  : t('channels.member_badge')}
              </small>
            </span>
            <button
              type="button"
              onClick={() => void onLeave(c.channel_id)}
              style={{ fontSize: '0.8em' }}
            >
              {t('channels.leave')}
            </button>
          </li>
        ))}
      </ul>

      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" onClick={() => setShowForm('group')}>
          {t('channels.new_group')}
        </button>
        <button type="button" onClick={() => setShowForm('direct')}>
          {t('channels.new_direct')}
        </button>
      </div>

      {showForm === 'group' && (
        <form onSubmit={onCreateGroup} style={{ display: 'grid', gap: 4 }}>
          <input
            placeholder={t('channels.name') ?? ''}
            value={groupName}
            onChange={(e) => setGroupName(e.target.value)}
            required
            minLength={1}
            maxLength={120}
          />
          <div style={{ display: 'flex', gap: 4 }}>
            <button type="submit" disabled={busy}>
              {t('channels.create')}
            </button>
            <button type="button" onClick={() => setShowForm(null)}>
              {t('channels.cancel')}
            </button>
          </div>
        </form>
      )}
      {showForm === 'direct' && (
        <form onSubmit={onCreateDirect} style={{ display: 'grid', gap: 4 }}>
          <input
            placeholder={t('channels.other_username') ?? ''}
            value={directUsername}
            onChange={(e) => setDirectUsername(e.target.value)}
            required
            minLength={1}
            maxLength={64}
          />
          <div style={{ display: 'flex', gap: 4 }}>
            <button type="submit" disabled={busy}>
              {t('channels.create')}
            </button>
            <button type="button" onClick={() => setShowForm(null)}>
              {t('channels.cancel')}
            </button>
          </div>
        </form>
      )}

      {error && <p style={{ color: 'crimson' }}>{error}</p>}
    </aside>
  )
}
