import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type Message,
  deleteMessage,
  editMessage,
  fetchHistory,
  sendMessage,
} from './api'
import { colors, radii } from '../theme'

interface Props {
  accessToken: string
  channelId: string
  myUserId: string
  /** Notify the parent when the conversation zone needs to remount
   *  (e.g. the user switches channels or leaves). */
  onClose?: () => void
}

type Pending = { clientRef: string; body: string; status: 'pending' | 'failed' }

/**
 * Conversation view for one channel. Implements the
 * *pending → sent → failed* state machine + lazy keyset history.
 *
 * Renders a top bar (channel name + leave), a scrollable message
 * list (oldest at top, newest at bottom), and a composer at the
 * bottom. New messages appear immediately in the "pending" slot
 * with a muted background; on success, they're keyed by the
 * server-side `rw_id`; on failure, a retry button appears.
 */
export function Conversation({ accessToken, channelId, myUserId }: Props) {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<Message[]>([])
  const [pending, setPending] = useState<Pending[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editBody, setEditBody] = useState('')
  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set())
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // ── Load the first page on mount + when channelId changes ─────────
  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      setMessages([])
      setPending([])
      try {
        const page = await fetchHistory(accessToken, channelId, null)
        if (!cancelled) {
          setMessages(page.items)
          setHasMore(page.next_cursor_id !== null)
        }
      } catch (err) {
        if (!cancelled) setError(String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [accessToken, channelId])

  // ── Auto-scroll to bottom on new messages ────────────────────────
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, pending.length])

  // ── Send a new message (state machine: pending → sent | failed) ─
  const onSend = useCallback(async () => {
    const body = draft.trim()
    if (!body) return
    const clientRef = `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setPending((p) => [...p, { clientRef, body, status: 'pending' }])
    setDraft('')
    try {
      const { message, wasReplay } = await sendMessage(
        accessToken, channelId, body, clientRef,
      )
      setPending((p) => p.filter((x) => x.clientRef !== clientRef))
      if (wasReplay) {
        // Server returned the existing message (same client_ref).
        // Make sure it's in the list — append if not, dedupe if so.
        setMessages((m) => (m.some((x) => x.rw_id === message.rw_id)
          ? m
          : [...m, message]))
      } else {
        setMessages((m) => [...m, message])
      }
    } catch {
      setPending((p) =>
        p.map((x) =>
          x.clientRef === clientRef ? { ...x, status: 'failed' } : x,
        ),
      )
    }
  }, [draft, accessToken, channelId])

  // ── Retry a failed send (uses the same client_ref → idempotent) ─
  const onRetry = useCallback(async (clientRef: string) => {
    const item = pending.find((p) => p.clientRef === clientRef)
    if (!item) return
    setPending((p) =>
      p.map((x) =>
        x.clientRef === clientRef ? { ...x, status: 'pending' } : x,
      ),
    )
    try {
      const { message } = await sendMessage(
        accessToken, channelId, item.body, clientRef,
      )
      setPending((p) => p.filter((x) => x.clientRef !== clientRef))
      setMessages((m) => [...m, message])
    } catch {
      setPending((p) =>
        p.map((x) =>
          x.clientRef === clientRef ? { ...x, status: 'failed' } : x,
        ),
      )
    }
  }, [pending, accessToken, channelId])

  // ── Lazy load older messages (keyset) ─────────────────────────────
  const onLoadMore = useCallback(async () => {
    if (!hasMore || loading || messages.length === 0) return
    setLoading(true)
    try {
      const oldest = messages[0]
      const page = await fetchHistory(accessToken, channelId, {
        ts: oldest.rw_created_at,
        id: oldest.rw_id,
      })
      setMessages((m) => [...page.items, ...m])
      setHasMore(page.next_cursor_id !== null)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [hasMore, loading, messages, accessToken, channelId])

  // ── Edit a message (only own messages) ────────────────────────────
  const onSaveEdit = useCallback(async (messageId: string) => {
    const body = editBody.trim()
    if (!body) return
    try {
      const updated = await editMessage(accessToken, messageId, body)
      setMessages((m) =>
        m.map((x) => (x.rw_id === messageId ? updated : x))
      )
      setEditingId(null)
      setEditBody('')
    } catch (err) {
      setError(String(err))
    }
  }, [editBody, accessToken])

  // ── Delete a message (logical delete, only own messages) ──────────
  const onDelete = useCallback(async (messageId: string) => {
    if (!confirm(t('messages.delete') + '?')) return
    try {
      await deleteMessage(accessToken, messageId)
      setDeletedIds((s) => new Set([...s, messageId]))
    } catch (err) {
      setError(String(err))
    }
  }, [accessToken, t])

  return (
    <section
      style={{
        display: 'grid',
        gridTemplateRows: 'auto 1fr auto',
        background: colors.background,
        color: colors.text,
        minHeight: 0,
        height: '100%',
        borderLeft: `1px solid ${colors.border}`,
      }}
    >
      <header
        style={{
          padding: '0.75rem 1rem',
          borderBottom: `1px solid ${colors.border}`,
          fontWeight: 600,
        }}
      >
        {t('messages.title')}
      </header>

      <div
        ref={listRef}
        style={{
          overflowY: 'auto',
          padding: '1rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
        }}
      >
        {hasMore && (
          <button
            type="button"
            onClick={() => void onLoadMore()}
            disabled={loading}
            style={{
              alignSelf: 'center',
              padding: '0.4rem 0.8rem',
              background: colors.input,
              color: colors.textMuted,
              border: 'none',
              borderRadius: radii.sm,
              cursor: 'pointer',
            }}
          >
            {loading ? t('messages.loading') : t('messages.load_more')}
          </button>
        )}

        {messages.length === 0 && pending.length === 0 && !loading && (
          <p style={{ color: colors.textMuted, textAlign: 'center' }}>
            {t('messages.empty_channel')}
          </p>
        )}

        {messages.map((m) => (
          <MessageRow
            key={m.rw_id}
            message={m}
            isMine={m.rw_author_id === myUserId}
            isDeleted={deletedIds.has(m.rw_id)}
            isEditing={editingId === m.rw_id}
            editBody={editBody}
            onEditStart={() => {
              setEditingId(m.rw_id)
              setEditBody(m.rw_body)
            }}
            onEditBodyChange={setEditBody}
            onSaveEdit={() => void onSaveEdit(m.rw_id)}
            onCancelEdit={() => {
              setEditingId(null)
              setEditBody('')
            }}
            onDelete={() => void onDelete(m.rw_id)}
          />
        ))}

        {pending.map((p) => (
          <PendingRow
            key={p.clientRef}
            body={p.body}
            status={p.status}
            onRetry={() => void onRetry(p.clientRef)}
          />
        ))}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          void onSend()
        }}
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.75rem 1rem',
          borderTop: `1px solid ${colors.border}`,
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t('messages.placeholder') ?? ''}
          style={{
            flex: 1,
            padding: '0.5rem 0.75rem',
            background: colors.input,
            color: colors.text,
            border: 'none',
            borderRadius: radii.md,
          }}
        />
        <button
          type="submit"
          disabled={!draft.trim()}
          style={{
            padding: '0.5rem 1rem',
            background: colors.blurple,
            color: colors.textHeader,
            border: 'none',
            borderRadius: radii.md,
            cursor: 'pointer',
          }}
        >
          {t('messages.send')}
        </button>
      </form>

      {error && (
        <p style={{ color: colors.danger, padding: '0.5rem 1rem' }}>
          {error}
        </p>
      )}
    </section>
  )
}

// ─── Sub-components ────────────────────────────────────────────────────


function MessageRow({
  message,
  isMine,
  isDeleted,
  isEditing,
  editBody,
  onEditStart,
  onEditBodyChange,
  onSaveEdit,
  onCancelEdit,
  onDelete,
}: {
  message: Message
  isMine: boolean
  isDeleted: boolean
  isEditing: boolean
  editBody: string
  onEditStart: () => void
  onEditBodyChange: (v: string) => void
  onSaveEdit: () => void
  onCancelEdit: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  return (
    <div
      data-testid={`message-${message.rw_id}`}
      style={{
        padding: '0.5rem 0.75rem',
        background: 'transparent',
        borderRadius: radii.sm,
        opacity: isDeleted ? 0.5 : 1,
      }}
    >
      {isEditing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            onSaveEdit()
          }}
          style={{ display: 'flex', gap: '0.4rem' }}
        >
          <input
            value={editBody}
            onChange={(e) => onEditBodyChange(e.target.value)}
            autoFocus
            style={{
              flex: 1,
              padding: '0.4rem 0.6rem',
              background: colors.input,
              color: colors.text,
              border: 'none',
              borderRadius: radii.sm,
            }}
          />
          <button
            type="submit"
            style={{
              background: colors.success,
              color: colors.textHeader,
              border: 'none',
              borderRadius: radii.sm,
              padding: '0.4rem 0.8rem',
              cursor: 'pointer',
            }}
          >
            {t('messages.save')}
          </button>
          <button
            type="button"
            onClick={onCancelEdit}
            style={{
              background: 'transparent',
              color: colors.textMuted,
              border: `1px solid ${colors.border}`,
              borderRadius: radii.sm,
              padding: '0.4rem 0.8rem',
              cursor: 'pointer',
            }}
          >
            {t('messages.cancel_edit')}
          </button>
        </form>
      ) : (
        <>
          <div
            style={{
              color: isDeleted ? colors.textMuted : colors.text,
              fontStyle: isDeleted ? 'italic' : 'normal',
            }}
          >
            {isDeleted ? t('messages.deleted') : message.rw_body}
            {message.rw_is_edited && !isDeleted && (
              <span style={{ color: colors.textMuted, fontSize: '0.85em' }}>
                {' '}
                {t('messages.edited_suffix')}
              </span>
            )}
          </div>
          {isMine && !isDeleted && (
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: 4 }}>
              <button
                type="button"
                onClick={onEditStart}
                style={{
                  fontSize: '0.8em',
                  background: 'transparent',
                  color: colors.textMuted,
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                {t('messages.edit')}
              </button>
              <button
                type="button"
                onClick={onDelete}
                style={{
                  fontSize: '0.8em',
                  background: 'transparent',
                  color: colors.textMuted,
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                {t('messages.delete')}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}


function PendingRow({
  body,
  status,
  onRetry,
}: {
  body: string
  status: 'pending' | 'failed'
  onRetry: () => void
}) {
  const { t } = useTranslation()
  return (
    <div
      data-testid="message-pending"
      data-status={status}
      style={{
        padding: '0.5rem 0.75rem',
        background: status === 'failed' ? 'rgba(237, 66, 69, 0.1)' : colors.pendingBg,
        border: status === 'failed' ? `1px solid ${colors.failedBorder}` : 'none',
        borderRadius: radii.sm,
      }}
    >
      <div style={{ color: colors.text, opacity: 0.7 }}>{body}</div>
      <div
        style={{
          fontSize: '0.8em',
          color: status === 'failed' ? colors.danger : colors.textMuted,
          marginTop: 4,
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
        }}
      >
        <span data-testid="pending-status">
          {status === 'pending' ? t('messages.sending') : t('messages.failed')}
        </span>
        {status === 'failed' && (
          <button
            type="button"
            onClick={onRetry}
            data-testid="retry-button"
            style={{
              background: colors.danger,
              color: colors.textHeader,
              border: 'none',
              borderRadius: radii.sm,
              padding: '0.2rem 0.6rem',
              cursor: 'pointer',
            }}
          >
            {t('messages.retry')}
          </button>
        )}
      </div>
    </div>
  )
}
