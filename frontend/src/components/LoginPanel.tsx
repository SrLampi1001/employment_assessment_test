import { useState } from "react";
import { useTranslation } from "react-i18next";
import { MessagesSquare } from "lucide-react";
import { describeError, useAuth } from "@/lib/auth";
import type { Locale } from "@/lib/types";

export function LoginPanel({ onLocaleChange }: { onLocaleChange: (locale: Locale) => void }) {
  const { t } = useTranslation();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [locale, setLocale] = useState<Locale>("es");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await login(username.trim(), password);
      } else {
        await register({
          username: username.trim(),
          display_name: displayName.trim(),
          password,
          locale,
        });
        onLocaleChange(locale);
      }
    } catch (err) {
      setError(describeError(err, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 inline-flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <MessagesSquare className="size-6" />
          </span>
          <h1 className="font-display text-2xl font-bold tracking-tight">{t("app.title")}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{t("auth.tagline")}</p>
        </div>

        <form
          onSubmit={submit}
          className="rounded-2xl border border-border bg-card p-6 shadow-panel"
        >
          <div className="mb-5 grid grid-cols-2 gap-1 rounded-xl bg-muted p-1 text-sm font-semibold">
            {(["login", "register"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => {
                  setMode(option);
                  setError(null);
                }}
                className={`rounded-lg py-2 transition-colors ${
                  mode === option
                    ? "bg-surface-raised text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {t(`auth.${option}`)}
              </button>
            ))}
          </div>

          <label className="mb-1 block text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            {t("auth.username")}
          </label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            maxLength={64}
            required
            autoComplete="username"
            className="mb-4 w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus:border-ring"
          />

          {mode === "register" && (
            <>
              <label className="mb-1 block text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {t("auth.display_name")}
              </label>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                maxLength={120}
                required
                className="mb-4 w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus:border-ring"
              />
              <label className="mb-1 block text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {t("auth.locale")}
              </label>
              <select
                value={locale}
                onChange={(e) => setLocale(e.target.value as Locale)}
                className="mb-4 w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus:border-ring"
              >
                <option value="es">Español</option>
                <option value="en">English</option>
              </select>
            </>
          )}

          <label className="mb-1 block text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            {t("auth.password")}
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            maxLength={128}
            required
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            className="w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus:border-ring"
          />

          {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

          <button
            type="submit"
            disabled={busy}
            className="mt-6 w-full rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {mode === "login" ? t("auth.submit_login") : t("auth.submit_register")}
          </button>

          <p className="mt-4 text-center text-xs text-muted-foreground">
            {mode === "login" ? t("auth.no_account") : t("auth.has_account")}{" "}
            <button
              type="button"
              onClick={() => setMode(mode === "login" ? "register" : "login")}
              className="font-semibold text-primary"
            >
              {mode === "login" ? t("auth.register") : t("auth.login")}
            </button>
          </p>
        </form>
      </div>
    </main>
  );
}
