import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import { AppShell } from "./components/AppShell";
import { LoginPanel } from "./components/LoginPanel";
import { AuthProvider, useAuth } from "./lib/auth";
import i18n, { LOCALE_STORAGE_KEY, initI18n } from "./lib/i18n";
import type { Locale } from "./lib/types";
import "./styles.css";

function App() {
  const stored = (window.localStorage.getItem(LOCALE_STORAGE_KEY) as Locale | null) ?? "es";
  initI18n(stored);

  return (
    <AuthProvider>
      <Gate />
      <Toaster theme="dark" position="top-center" />
    </AuthProvider>
  );
}

function Gate() {
  const { tokens, ready, setLocale } = useAuth();

  if (!ready) return <div className="min-h-screen bg-background" />;
  if (!tokens) {
    return (
      <LoginPanel
        onLocaleChange={(locale) => {
          window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
          void i18n.changeLanguage(locale);
          setLocale(locale);
        }}
      />
    );
  }
  return <AppShell />;
}

const container = document.getElementById("root");
if (!container) throw new Error("Root container missing in index.html");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
