import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LogOut, Menu, MessagesSquare, Sparkles, X } from "lucide-react";
import { ChannelList } from "./ChannelList";
import { Conversation } from "./Conversation";
import { CopilotPanel } from "./CopilotPanel";
import { useAuth } from "@/lib/auth";
import { LOCALE_STORAGE_KEY } from "@/lib/i18n";
import type { Channel, Locale } from "@/lib/types";

export function AppShell() {
  const { t, i18n } = useTranslation();
  const { user, logout, setLocale } = useAuth();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selected, setSelected] = useState<Channel | null>(null);
  const [channelsVersion, setChannelsVersion] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [copilotOpen, setCopilotOpen] = useState(false);

  const totalUnread = useMemo(
    () => channels.reduce((sum, channel) => sum + channel.unread_count, 0),
    [channels],
  );

  const bumpChannels = useCallback(() => setChannelsVersion((v) => v + 1), []);

  const onChannelsLoaded = useCallback((items: Channel[]) => {
    setChannels(items);
    setSelected((prev) =>
      prev ? (items.find((c) => c.channel_id === prev.channel_id) ?? prev) : prev,
    );
  }, []);

  function changeLocale(locale: Locale) {
    void i18n.changeLanguage(locale);
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    setLocale(locale);
  }

  useEffect(() => {
    if (selected) setSidebarOpen(false);
  }, [selected]);

  const currentLocale = (i18n.language === "en" ? "en" : "es") as Locale;

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 items-center gap-3 border-b border-border bg-sidebar px-4 py-2.5">
        <button
          onClick={() => setSidebarOpen(true)}
          title={t("app.menu")}
          className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground lg:hidden"
        >
          <Menu className="size-5" />
        </button>
        <MessagesSquare className="size-5 text-primary" />
        <h1 className="font-display text-sm font-bold tracking-tight">{t("app.title")}</h1>
        {totalUnread > 0 && (
          <span
            data-testid="total-unread"
            className="rounded-full bg-destructive px-2 py-0.5 text-[10px] font-bold text-destructive-foreground"
          >
            {t("app.total_unread", { count: totalUnread })}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <div
            className="flex overflow-hidden rounded-lg border border-border text-[11px] font-bold"
            data-testid="lang-toggle"
          >
            {(["es", "en"] as const).map((locale) => (
              <button
                key={locale}
                onClick={() => changeLocale(locale)}
                className={`px-2 py-1 transition-colors ${
                  currentLocale === locale
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {locale.toUpperCase()}
              </button>
            ))}
          </div>
          <button
            onClick={() => setCopilotOpen(true)}
            title={t("copilot.title")}
            className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground xl:hidden"
          >
            <Sparkles className="size-5" />
          </button>
          <button
            onClick={logout}
            title={t("app.logout")}
            data-testid="logout-button"
            className="rounded-lg p-1.5 text-muted-foreground hover:text-destructive"
          >
            <LogOut className="size-5" />
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <div
          className={`fixed inset-0 z-40 bg-background/70 lg:hidden ${sidebarOpen ? "" : "hidden"}`}
          onClick={() => setSidebarOpen(false)}
        />
        <aside
          className={`fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col border-r border-border bg-sidebar pt-3 transition-transform lg:static lg:z-auto lg:w-[240px] lg:translate-x-0 xl:w-[280px] ${
            sidebarOpen ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <div className="flex items-center justify-between px-4 pb-2 lg:hidden">
            <span className="font-display text-sm font-bold">{t("channels.title")}</span>
            <button onClick={() => setSidebarOpen(false)} className="text-muted-foreground">
              <X className="size-4" />
            </button>
          </div>

          <ChannelList
            selectedChannelId={selected?.channel_id ?? null}
            onSelect={(channel) => setSelected(channel)}
            refreshTrigger={channelsVersion}
            onChannelsLoaded={onChannelsLoaded}
          />

          <footer className="border-t border-sidebar-border px-4 py-3">
            <p className="text-[10px] tracking-widest text-muted-foreground uppercase">
              {t("app.signed_in_as")}
            </p>
            <p className="truncate text-sm font-semibold">{user?.display_name}</p>
            <p className="truncate text-xs text-muted-foreground">
              @{user?.username} · {currentLocale.toUpperCase()}
            </p>
            <button
              onClick={logout}
              className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-destructive"
            >
              <LogOut className="size-3.5" />
              {t("app.logout")}
            </button>
          </footer>
        </aside>

        {/* Main */}
        {selected ? (
          <Conversation
            key={selected.channel_id}
            channel={selected}
            onOpenCopilot={() => setCopilotOpen(true)}
            onMessagesChanged={bumpChannels}
          />
        ) : (
          <section className="flex min-w-0 flex-1 items-center justify-center px-6 text-center">
            <p className="text-sm text-muted-foreground">{t("app.no_channel")}</p>
          </section>
        )}

        {/* Copilot */}
        <div className="hidden w-[360px] shrink-0 xl:flex">
          <CopilotPanel contextKey={selected?.channel_id ?? null} />
        </div>
        {copilotOpen && (
          <div className="fixed inset-0 z-50 flex justify-end bg-background/70 xl:hidden">
            <div
              className="flex-1"
              onClick={() => setCopilotOpen(false)}
              aria-hidden
            />
            <div className="relative flex w-full max-w-[360px] flex-col bg-sidebar">
              <button
                onClick={() => setCopilotOpen(false)}
                className="absolute top-3 right-3 z-10 text-muted-foreground"
              >
                <X className="size-4" />
              </button>
              <CopilotPanel contextKey={selected?.channel_id ?? null} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
