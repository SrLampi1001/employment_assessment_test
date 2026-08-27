import { useTranslation } from 'react-i18next'
import { clearTokens, getTokens, getUser } from './auth/api'
import { LoginPanel } from './auth/LoginPanel'
import { ChannelList } from './channels/ChannelList'
import { useState } from 'react'

export default function App() {
  const { t, i18n } = useTranslation()
  const tokens = getTokens()
  const cachedUser = getUser()
  const [username, setUsername] = useState<string | null>(
    cachedUser?.rw_username ?? null,
  )

  function onLogout() {
    clearTokens()
    setUsername(null)
  }

  return (
    <main
      style={{
        fontFamily: 'system-ui, sans-serif',
        padding: '2rem',
        display: 'grid',
        gap: '1.5rem',
      }}
    >
      <header style={{ display: 'flex', justifyContent: 'space-between' }}>
        <h1>{t('app.title')}</h1>
        <div>
          <button
            type="button"
            onClick={() =>
              i18n.changeLanguage(i18n.language === 'es' ? 'en' : 'es')
            }
            style={{ marginRight: 8 }}
          >
            {i18n.language === 'es' ? 'EN' : 'ES'}
          </button>
          {username && (
            <button type="button" onClick={onLogout}>
              {t('auth.logout')}
            </button>
          )}
        </div>
      </header>

      {tokens && username ? (
        <ChannelList accessToken={tokens.access_token} username={username} />
      ) : (
        <LoginPanel onAuthenticated={(u) => setUsername(u)} />
      )}
    </main>
  )
}
