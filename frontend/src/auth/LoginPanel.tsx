import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { login, register, setUser } from './api'

interface Props {
  onAuthenticated: (username: string) => void
}

export function LoginPanel({ onAuthenticated }: Props) {
  const { t, i18n } = useTranslation()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'register') {
        await register(username, password, i18n.language as 'es' | 'en')
      }
      await login(username, password)
      setUser({
        rw_id: '',
        rw_username: username,
        rw_display_name: username,
        rw_locale: i18n.language as 'es' | 'en',
      })
      onAuthenticated(username)
    } catch {
      setError(t('auth.invalid_credentials'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      style={{ display: 'grid', gap: '0.75rem', maxWidth: 320 }}
    >
      <h2>{t('auth.login')}</h2>
      <label>
        {t('auth.username')}
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
          autoComplete="username"
        />
      </label>
      <label>
        {t('auth.password')}
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
        />
      </label>
      <button type="submit" disabled={busy}>
        {t('auth.submit')}
      </button>
      <button
        type="button"
        onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
      >
        {mode === 'login'
          ? t('app.mode.login')
          : t('app.mode.register')}
      </button>
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
    </form>
  )
}
