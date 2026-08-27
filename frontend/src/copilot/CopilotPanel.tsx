import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type Citation,
  type CopilotAnswer,
  askCopilot,
} from './api'
import { colors, radii } from '../theme'

interface Props {
  accessToken: string
  /** Bump when the user asks another question or selects a different
   *  channel — the parent re-renders and the panel resets. */
  contextKey?: string
}

/**
 * AI Copilot panel (Phase 6, ARCHITECTURE.md §8 — "three required
 * zones: conversations list · copilot panel · user profile").
 *
 * Renders the prompt input, the in-flight indicator, the answer with
 * its citation chips, and one of four denial / inference banners
 * depending on `answer.denial_code`. The banner colour + copy match
 * `references/denial-taxonomy.md` verbatim.
 */
export function CopilotPanel({ accessToken, contextKey }: Props) {
  const { t } = useTranslation()
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset when the context (channel + version) changes so the panel
  // doesn't carry over a stale answer from the previous channel.
  if (contextKey !== undefined && contextKey !== (answer as AnswerWithContext | null)?._contextKey) {
    if (answer !== null) {
      setAnswer(null)
      setError(null)
      setQuestion('')
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q) return
    setBusy(true)
    setError(null)
    try {
      const ans = await askCopilot(accessToken, q, 5)
      // Tag the answer with the current context so the reset check
      // above doesn't see it as "stale" before it renders.
      const tagged: AnswerWithContext = ans
      tagged._contextKey = contextKey
      setAnswer(tagged)
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside
      style={{
        display: 'grid',
        gridTemplateRows: 'auto 1fr auto',
        background: colors.sidebar,
        color: colors.text,
        borderLeft: `1px solid ${colors.border}`,
        minHeight: 0,
      }}
    >
      <header
        style={{
          padding: '0.75rem 1rem',
          borderBottom: `1px solid ${colors.border}`,
          fontWeight: 600,
        }}
      >
        {t('copilot.title')}
      </header>

      <div
        style={{
          padding: '0.75rem 1rem',
          overflowY: 'auto',
          display: 'grid',
          gap: '0.75rem',
          minHeight: 0,
        }}
      >
        {answer === null && !busy && (
          <p
            style={{
              color: colors.textMuted,
              fontSize: '0.9em',
              margin: 0,
            }}
          >
            {t('copilot.empty')}
          </p>
        )}

        {busy && (
          <p
            data-testid="copilot-loading"
            style={{ color: colors.textMuted, margin: 0 }}
          >
            {t('copilot.thinking')}
          </p>
        )}

        {answer !== null && (
          <>
            {answer.denial_code !== null && (
              <DenialBanner denialCode={answer.denial_code} />
            )}
            <div
              data-testid="copilot-answer"
              style={{
                whiteSpace: 'pre-wrap',
                fontSize: '0.95em',
                lineHeight: 1.5,
              }}
            >
              {answer.text}
            </div>
            {answer.citations.length > 0 && (
              <div
                data-testid="copilot-citations"
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 4,
                  marginTop: 4,
                }}
              >
                {answer.citations.map((c) => (
                  <CitationChip key={c.rw_id} citation={c} />
                ))}
              </div>
            )}
            <p
              style={{
                color: colors.textMuted,
                fontSize: '0.7em',
                margin: '4px 0 0 0',
              }}
            >
              {t('copilot.prompt_version', {
                v: answer.prompt_version,
              })}{' '}
              · {t('copilot.confidence_label', {
                c: answer.confidence,
              })}
            </p>
          </>
        )}

        {error && (
          <p
            style={{
              color: colors.danger,
              fontSize: '0.85em',
              margin: 0,
            }}
          >
            {t('copilot.error')}: {error}
          </p>
        )}
      </div>

      <form
        onSubmit={onSubmit}
        data-testid="copilot-form"
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.75rem 1rem',
          borderTop: `1px solid ${colors.border}`,
        }}
      >
        <input
          data-testid="copilot-input"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t('copilot.placeholder') ?? ''}
          disabled={busy}
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
          data-testid="copilot-submit"
          disabled={busy || !question.trim()}
          style={{
            padding: '0.5rem 1rem',
            background: colors.blurple,
            color: colors.textHeader,
            border: 'none',
            borderRadius: radii.md,
            cursor: 'pointer',
          }}
        >
          {t('copilot.ask')}
        </button>
      </form>
    </aside>
  )
}

interface AnswerWithContext extends CopilotAnswer {
  _contextKey?: string
}

function DenialBanner({ denialCode }: { denialCode: string }) {
  const { t } = useTranslation()
  // Per `references/denial-taxonomy.md`: each code gets its own
  // colour. We map them to the palette tokens in `theme.ts`.
  const palette: Record<string, { bg: string; fg: string; label: string }> = {
    'deny:no-permission': {
      bg: colors.danger,
      fg: '#FFFFFF',
      label: t('copilot.deny_no_permission'),
    },
    'deny:out-of-scope': {
      bg: colors.textMuted,
      fg: '#FFFFFF',
      label: t('copilot.deny_out_of_scope'),
    },
    'deny:insufficient-context': {
      bg: '#FAA61A',
      fg: '#000000',
      label: t('copilot.deny_insufficient_context'),
    },
    'infer:low-confidence': {
      bg: '#ED8936',
      fg: '#000000',
      label: t('copilot.infer_low_confidence'),
    },
  }
  const p = palette[denialCode] ?? {
    bg: colors.textMuted,
    fg: '#FFFFFF',
    label: denialCode,
  }
  return (
    <div
      data-testid={`copilot-banner-${denialCode}`}
      style={{
        padding: '0.5rem 0.75rem',
        background: p.bg,
        color: p.fg,
        borderRadius: radii.sm,
        fontSize: '0.85em',
        fontWeight: 600,
      }}
    >
      {denialCode} — {p.label}
    </div>
  )
}

// ─── Citation chip ─────────────────────────────────────────────────────


function CitationChip({ citation }: { citation: Citation }) {
  return (
    <span
      data-testid={`citation-${citation.rw_id}`}
      title={citation.snippet}
      style={{
        fontSize: '0.75em',
        padding: '0.2rem 0.5rem',
        background: colors.input,
        color: colors.textMuted,
        borderRadius: 8,
        cursor: 'help',
      }}
    >
      [{citation.rw_id.slice(0, 8)}]
    </span>
  )
}