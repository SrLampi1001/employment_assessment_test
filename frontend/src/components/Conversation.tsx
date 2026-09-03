import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Hash,
  Loader2,
  MessageCircle,
  Pencil,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError, newClientRef, query } from "@/lib/api";
import { describeError, useAuth } from "@/lib/auth";
import {
  CHANNEL_KIND_GROUP,
  type Channel,
  type Message,
  type MessagePage,
  type PendingMessage,
  type SearchHit,
  type SearchResponse,
} from "@/lib/types";

const PAGE_SIZE = 50;

type Translate = ReturnType<typeof useTranslation>["t"];

function formatTimestamp(iso: string, t: Translate) {
  const date = new Date(iso);
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const isToday = new Date().toDateString() === date.toDateString();
  if (isToday) return t("messages.today_at", { time });
  return `${date.toLocaleDateString(undefined, { day: "2-digit", month: "short" })} · ${time}`;
}

interface Props {
  channel: Channel;
  onOpenCopilot: () => void;
  onMessagesChanged: () => void;
}

export function Conversation({ channel, onOpenCopilot, onMessagesChanged }: Props) {
  const { t } = useTranslation();
  const { request, user } = useAuth();

  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState<PendingMessage[]>([]);
  const [cursor, setCursor] = useState<{ created_at: string; id: string } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editBody, setEditBody] = useState("");
  const [deleting, setDeleting] = useState<Message | null>(null);
  const [deleteReason, setDeleteReason] = useState("user-deleted");

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);

  const [newMessagesBelow, setNewMessagesBelow] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
    setNewMessagesBelow(false);
  }, []);

  const markRead = useCallback(async () => {
    try {
      await request<{ inserted: number }>(`/api/v1/channels/${channel.channel_id}/read`, {
        method: "POST",
      });
      onMessagesChanged();
    } catch {
      /* non-fatal */
    }
  }, [channel.channel_id, request, onMessagesChanged]);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await request<MessagePage>(
        `/api/v1/channels/${channel.channel_id}/messages${query({ limit: PAGE_SIZE })}`,
      );
      const items = [...(data?.items ?? [])].reverse();
      setMessages(items);
      setHasMore(Boolean(data?.next_cursor_created_at));
      setCursor(
        data?.next_cursor_created_at && data?.next_cursor_id
          ? { created_at: data.next_cursor_created_at, id: data.next_cursor_id }
          : null,
      );
      requestAnimationFrame(() => scrollToBottom());
    } catch (err) {
      setError(describeError(err, t));
    } finally {
      setLoading(false);
    }
  }, [channel.channel_id, request, t, scrollToBottom]);

  useEffect(() => {
    setMessages([]);
    setPending([]);
    setSearchOpen(false);
    setSearchTerm("");
    setSearchHits([]);
    setEditingId(null);
    void loadInitial().then(() => void markRead());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel.channel_id]);

  // Poll for new messages in the open channel.
  useEffect(() => {
    const id = window.setInterval(async () => {
      try {
        const { data } = await request<MessagePage>(
          `/api/v1/channels/${channel.channel_id}/messages${query({ limit: PAGE_SIZE })}`,
        );
        const items = [...(data?.items ?? [])].reverse();
        setMessages((prev) => {
          const known = new Set(prev.map((m) => m.rw_id));
          const fresh = items.filter((m) => !known.has(m.rw_id));
          if (fresh.length === 0) {
            return prev.map((m) => items.find((i) => i.rw_id === m.rw_id) ?? m);
          }
          if (atBottomRef.current) requestAnimationFrame(() => scrollToBottom("smooth"));
          else setNewMessagesBelow(true);
          return [...prev, ...fresh];
        });
      } catch {
        /* silent poll failure */
      }
    }, 5000);
    return () => window.clearInterval(id);
  }, [channel.channel_id, request, scrollToBottom]);

  async function loadMore() {
    if (!cursor) return;
    const el = scrollRef.current;
    const previousHeight = el?.scrollHeight ?? 0;
    try {
      const { data } = await request<MessagePage>(
        `/api/v1/channels/${channel.channel_id}/messages${query({
          cursor_ts: cursor.created_at,
          cursor_id: cursor.id,
          limit: PAGE_SIZE,
        })}`,
      );
      const older = [...(data?.items ?? [])].reverse();
      setMessages((prev) => [...older, ...prev]);
      setHasMore(Boolean(data?.next_cursor_created_at));
      setCursor(
        data?.next_cursor_created_at && data?.next_cursor_id
          ? { created_at: data.next_cursor_created_at, id: data.next_cursor_id }
          : null,
      );
      requestAnimationFrame(() => {
        if (el) el.scrollTop = el.scrollHeight - previousHeight;
      });
    } catch (err) {
      toast.error(describeError(err, t));
    }
  }

  const sendWithRef = useCallback(
    async (body: string, clientRef: string) => {
      try {
        const { data } = await request<Message>(`/api/v1/channels/${channel.channel_id}/messages`, {
          method: "POST",
          body: JSON.stringify({ body, client_ref: clientRef }),
        });
        setPending((prev) => prev.filter((p) => p.client_ref !== clientRef));
        setMessages((prev) => (prev.some((m) => m.rw_id === data.rw_id) ? prev : [...prev, data]));
        onMessagesChanged();
        requestAnimationFrame(() => scrollToBottom("smooth"));
      } catch (err) {
        setPending((prev) =>
          prev.map((p) => (p.client_ref === clientRef ? { ...p, state: "failed" } : p)),
        );
        if (!(err instanceof ApiError && err.status === 401)) toast.error(describeError(err, t));
      }
    },
    [channel.channel_id, request, t, scrollToBottom, onMessagesChanged],
  );

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const body = draft.trim();
    if (!body) return;
    const clientRef = newClientRef();
    setDraft("");
    setPending((prev) => [
      ...prev,
      { client_ref: clientRef, body, state: "pending", created_at: new Date().toISOString() },
    ]);
    requestAnimationFrame(() => scrollToBottom("smooth"));
    await sendWithRef(body, clientRef);
  }

  async function retry(item: PendingMessage) {
    setPending((prev) =>
      prev.map((p) => (p.client_ref === item.client_ref ? { ...p, state: "pending" } : p)),
    );
    await sendWithRef(item.body, item.client_ref);
  }

  async function saveEdit(message: Message) {
    const body = editBody.trim();
    if (!body) return;
    try {
      const { data } = await request<Message>(`/api/v1/messages/${message.rw_id}`, {
        method: "PATCH",
        body: JSON.stringify({ body }),
      });
      setMessages((prev) => prev.map((m) => (m.rw_id === data.rw_id ? data : m)));
      setEditingId(null);
    } catch (err) {
      toast.error(describeError(err, t));
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    try {
      await request(`/api/v1/messages/${deleting.rw_id}/delete`, {
        method: "POST",
        body: JSON.stringify({ reason: deleteReason || "user-deleted" }),
      });
      setMessages((prev) => prev.filter((m) => m.rw_id !== deleting.rw_id));
      setDeleting(null);
      setDeleteReason("user-deleted");
    } catch (err) {
      toast.error(describeError(err, t));
    }
  }

  useEffect(() => {
    if (!searchOpen) return;
    const term = searchTerm.trim();
    if (!term) {
      setSearchHits([]);
      return;
    }
    setSearching(true);
    const handle = window.setTimeout(async () => {
      try {
        const { data } = await request<SearchResponse>(
          `/api/v1/channels/${channel.channel_id}/search${query({ q: term, limit: 20 })}`,
        );
        setSearchHits(data?.items ?? []);
      } catch (err) {
        toast.error(describeError(err, t));
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchOpen, searchTerm, channel.channel_id, request, t]);

  function jumpToMessage(id: string) {
    const el = document.getElementById(`message-${id}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("ring-1", "ring-ring");
      window.setTimeout(() => el.classList.remove("ring-1", "ring-ring"), 1600);
    }
  }

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        {channel.kind === CHANNEL_KIND_GROUP ? (
          <Hash className="size-4 text-muted-foreground" />
        ) : (
          <MessageCircle className="size-4 text-muted-foreground" />
        )}
        <div className="min-w-0">
          <h1 className="truncate font-display text-sm font-bold">{channel.name}</h1>
          <p className="text-xs text-muted-foreground">
            {channel.kind === CHANNEL_KIND_GROUP ? t("channels.group") : t("channels.direct")}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setSearchOpen((open) => !open)}
            title={t("messages.search")}
            data-testid="toggle-search-button"
            className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <Search className="size-4" />
          </button>
          <button
            onClick={onOpenCopilot}
            title={t("copilot.title")}
            className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground xl:hidden"
          >
            <Sparkles className="size-4" />
          </button>
        </div>
      </header>

      {searchOpen && (
        <div className="border-b border-border bg-surface px-4 py-3">
          <div className="flex items-center gap-2">
            <input
              autoFocus
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder={t("messages.search_placeholder")}
              data-testid="search-input"
              className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:border-ring"
            />
            <button
              onClick={() => setSearchOpen(false)}
              className="rounded-lg p-2 text-muted-foreground hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="mt-2 max-h-56 overflow-y-auto scroll-slim">
            {searching && <p className="py-2 text-xs text-muted-foreground">…</p>}
            {!searching && searchTerm.trim() && searchHits.length === 0 && (
              <p className="py-2 text-xs text-muted-foreground">{t("messages.no_results")}</p>
            )}
            <ul className="space-y-1" data-testid="search-results">
              {searchHits.map((hit) => (
                <li key={hit.rw_id} data-testid={`search-hit-${hit.rw_id}`}>
                  <button
                    onClick={() => jumpToMessage(hit.rw_id)}
                    className="w-full rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-secondary"
                  >
                    <span className="block text-[11px] text-muted-foreground">
                      {formatTimestamp(hit.rw_created_at, t)}
                    </span>
                    <span dangerouslySetInnerHTML={{ __html: hit.rw_highlight }} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
          if (atBottomRef.current) setNewMessagesBelow(false);
        }}
        className="relative min-h-0 flex-1 overflow-y-auto px-4 py-4 scroll-slim"
      >
        {hasMore && (
          <div className="mb-4 flex justify-center">
            <button
              onClick={() => void loadMore()}
              className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground"
            >
              {t("messages.load_more")}
            </button>
          </div>
        )}

        {loading && messages.length === 0 && (
          <div className="flex justify-center py-6 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
          </div>
        )}

        {error && <p className="py-2 text-sm text-destructive">{error}</p>}

        {!loading && messages.length === 0 && pending.length === 0 && !error && (
          <p className="py-10 text-center text-sm text-muted-foreground">
            {t("messages.no_messages")}
          </p>
        )}

        <ul className="space-y-1">
          {messages.map((message) => (
            <li
              key={message.rw_id}
              id={`message-${message.rw_id}`}
              data-testid={`message-${message.rw_id}`}
              className="group rounded-lg px-2 py-1.5 transition-colors hover:bg-surface"
            >
              <div className="flex items-baseline gap-2">
                <span className="text-sm font-semibold">
                  {message.is_mine
                    ? t("messages.you")
                    : user && message.rw_author_id === user.actor_id
                      ? user.display_name
                      : message.rw_author_id.slice(0, 8)}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {formatTimestamp(message.rw_created_at, t)}
                </span>
                {message.rw_is_edited && (
                  <span className="text-[11px] text-muted-foreground italic">
                    ({t("messages.edited")})
                  </span>
                )}
                {message.is_mine && editingId !== message.rw_id && (
                  <span className="ml-auto flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      onClick={() => {
                        setEditingId(message.rw_id);
                        setEditBody(message.rw_body);
                      }}
                      title={t("messages.edit")}
                      className="rounded-md p-1 text-muted-foreground hover:text-foreground"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      onClick={() => setDeleting(message)}
                      title={t("messages.delete")}
                      className="rounded-md p-1 text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </span>
                )}
              </div>
              {editingId === message.rw_id ? (
                <div className="mt-1">
                  <textarea
                    value={editBody}
                    onChange={(e) => setEditBody(e.target.value)}
                    maxLength={8000}
                    rows={2}
                    className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:border-ring"
                  />
                  <div className="mt-1 flex gap-2">
                    <button
                      onClick={() => void saveEdit(message)}
                      className="rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground"
                    >
                      {t("messages.save")}
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="rounded-md px-2.5 py-1 text-xs text-muted-foreground"
                    >
                      {t("messages.cancel")}
                    </button>
                  </div>
                </div>
              ) : (
                <p className="mt-0.5 text-sm whitespace-pre-wrap text-foreground/90">
                  {message.rw_body}
                </p>
              )}
            </li>
          ))}

          {pending.map((item) => (
            <li
              key={item.client_ref}
              data-testid="message-pending"
              data-status={item.state}
              className="rounded-lg px-2 py-1.5 opacity-80"
            >
              <div className="flex items-baseline gap-2">
                <span className="text-sm font-semibold">{t("messages.you")}</span>
                <span data-testid="pending-status" className="text-[11px] text-muted-foreground">
                  {item.state === "failed" ? t("messages.failed") : t("messages.pending")}
                </span>
                {item.state === "pending" && (
                  <Loader2 className="size-3 animate-spin text-muted-foreground" />
                )}
                {item.state === "failed" && (
                  <button
                    onClick={() => void retry(item)}
                    data-testid="retry-button"
                    className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[11px] font-semibold text-caution"
                  >
                    <RefreshCw className="size-3" />
                    {t("messages.retry")}
                  </button>
                )}
              </div>
              <p className="mt-0.5 text-sm whitespace-pre-wrap text-foreground/70">{item.body}</p>
            </li>
          ))}
        </ul>
      </div>

      {newMessagesBelow && (
        <button
          onClick={() => scrollToBottom("smooth")}
          className="mx-4 mb-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground"
        >
          {t("messages.new_messages")}
        </button>
      )}

      <form onSubmit={send} className="flex items-end gap-2 border-t border-border px-4 py-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(e);
            }
          }}
          rows={1}
          maxLength={8000}
          placeholder={t("messages.send_placeholder")}
          className="max-h-40 min-h-11 w-full resize-none rounded-xl border border-input bg-surface px-3 py-3 text-sm outline-none focus:border-ring"
        />
        <button
          type="submit"
          title={t("messages.send")}
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-opacity hover:opacity-90"
        >
          <Send className="size-4" />
        </button>
      </form>

      {deleting && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 px-4">
          <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-5 shadow-panel">
            <h2 className="font-display text-base font-bold">{t("messages.delete_title")}</h2>
            <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">{deleting.rw_body}</p>
            <input
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              placeholder={t("messages.delete_reason")}
              className="mt-4 w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus:border-ring"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeleting(null)}
                className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground"
              >
                {t("messages.cancel")}
              </button>
              <button
                onClick={() => void confirmDelete()}
                className="rounded-lg bg-destructive px-3 py-1.5 text-sm font-semibold text-destructive-foreground"
              >
                {t("messages.confirm_delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
