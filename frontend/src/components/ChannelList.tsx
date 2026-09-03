import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hash, MessageCircle, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { describeError, useAuth } from "@/lib/auth";
import { query } from "@/lib/api";
import {
  CHANNEL_KIND_GROUP,
  type Channel,
  type ChannelListResponse,
  type UserSearchResult,
} from "@/lib/types";

interface Props {
  selectedChannelId: string | null;
  onSelect: (channel: Channel) => void;
  refreshTrigger: number;
  onChannelsLoaded: (channels: Channel[]) => void;
}

export function ChannelList({
  selectedChannelId,
  onSelect,
  refreshTrigger,
  onChannelsLoaded,
}: Props) {
  const { t } = useTranslation();
  const { request } = useAuth();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<"group" | "direct" | null>(null);
  const [groupName, setGroupName] = useState("");
  const [userQuery, setUserQuery] = useState("");
  const [userResults, setUserResults] = useState<UserSearchResult[]>([]);
  const loadedRef = useRef(onChannelsLoaded);
  loadedRef.current = onChannelsLoaded;

  const load = useCallback(async () => {
    try {
      const { data } = await request<ChannelListResponse>("/api/v1/channels");
      const items = data?.items ?? [];
      setChannels(items);
      loadedRef.current(items);
      setError(null);
    } catch (err) {
      setError(describeError(err, t));
    }
  }, [request, t]);

  useEffect(() => {
    void load();
  }, [load, refreshTrigger]);

  useEffect(() => {
    const id = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (creating !== "direct" || userQuery.trim().length === 0) {
      setUserResults([]);
      return;
    }
    const handle = window.setTimeout(async () => {
      try {
        const { data } = await request<UserSearchResult[]>(
          `/api/v1/users/search${query({ q: userQuery.trim(), limit: 8 })}`,
        );
        setUserResults(data ?? []);
      } catch {
        setUserResults([]);
      }
    }, 300);
    return () => window.clearTimeout(handle);
  }, [creating, userQuery, request]);

  async function createGroup(event: React.FormEvent) {
    event.preventDefault();
    try {
      const { data } = await request<Channel>("/api/v1/channels/group", {
        method: "POST",
        body: JSON.stringify({ name: groupName.trim() }),
      });
      setGroupName("");
      setCreating(null);
      await load();
      if (data) onSelect(data);
    } catch (err) {
      toast.error(describeError(err, t));
    }
  }

  async function createDirect(username: string) {
    try {
      const { data } = await request<Channel>("/api/v1/channels/direct", {
        method: "POST",
        body: JSON.stringify({ other_username: username }),
      });
      setUserQuery("");
      setUserResults([]);
      setCreating(null);
      await load();
      if (data) onSelect(data);
    } catch (err) {
      toast.error(describeError(err, t));
    }
  }

  async function leave(channel: Channel) {
    if (!window.confirm(t("channels.leave_confirm"))) return;
    try {
      await request(`/api/v1/channels/${channel.channel_id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      toast.error(describeError(err, t));
    }
  }

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      data-testid="channel-list"
    >
      <div className="flex items-center justify-between px-4 pb-2">
        <h2 className="text-xs font-bold tracking-widest text-muted-foreground uppercase">
          {t("channels.title")}
        </h2>
        <div className="flex gap-1">
          <button
            onClick={() => setCreating(creating === "group" ? null : "group")}
            title={t("channels.create_group")}
            data-testid="new-group-button"
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
          >
            <Plus className="size-4" />
          </button>
          <button
            onClick={() => setCreating(creating === "direct" ? null : "direct")}
            title={t("channels.create_direct")}
            data-testid="new-direct-button"
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
          >
            <MessageCircle className="size-4" />
          </button>
        </div>
      </div>

      {creating === "group" && (
        <form
          onSubmit={createGroup}
          data-testid="new-group-form"
          className="mx-3 mb-2 rounded-lg bg-sidebar-accent p-2"
        >
          <input
            autoFocus
            value={groupName}
            onChange={(e) => setGroupName(e.target.value)}
            maxLength={120}
            required
            placeholder={t("channels.channel_name")}
            data-testid="new-group-name"
            className="w-full rounded-md border border-input bg-surface px-2 py-1.5 text-sm outline-none focus:border-ring"
          />
          <div className="mt-2 flex gap-2">
            <button
              type="submit"
              data-testid="create-group-submit"
              className="rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground"
            >
              {t("channels.create")}
            </button>
            <button
              type="button"
              onClick={() => setCreating(null)}
              className="rounded-md px-2.5 py-1 text-xs text-muted-foreground"
            >
              {t("channels.cancel")}
            </button>
          </div>
        </form>
      )}

      {creating === "direct" && (
        <div className="mx-3 mb-2 rounded-lg bg-sidebar-accent p-2" data-testid="new-direct-form">
          <input
            autoFocus
            value={userQuery}
            onChange={(e) => setUserQuery(e.target.value)}
            placeholder={t("channels.search_user")}
            data-testid="new-direct-username"
            className="w-full rounded-md border border-input bg-surface px-2 py-1.5 text-sm outline-none focus:border-ring"
          />
          <ul className="mt-2 max-h-40 overflow-y-auto scroll-slim">
            {userResults.map((user) => (
              <li key={user.rw_id}>
                <button
                  onClick={() => void createDirect(user.rw_username)}
                  className="w-full rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-surface-raised"
                >
                  <span className="font-medium">{user.rw_display_name}</span>{" "}
                  <span className="text-xs text-muted-foreground">@{user.rw_username}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-4 scroll-slim">
        {error && <p className="px-2 py-3 text-xs text-destructive">{error}</p>}
        {!error && channels.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted-foreground">{t("channels.empty")}</p>
        )}
        <ul className="space-y-0.5">
          {channels.map((channel) => {
            const active = channel.channel_id === selectedChannelId;
            return (
              <li key={channel.channel_id} className="group relative">
                <button
                  onClick={() => onSelect(channel)}
                  data-testid={`channel-${channel.name}`}
                  data-channel-id={channel.channel_id}
                  data-unread-count={channel.unread_count}
                  data-active={active ? "true" : "false"}
                  className={`flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
                  }`}
                >
                  {channel.kind === CHANNEL_KIND_GROUP ? (
                    <Hash className="size-4 shrink-0" />
                  ) : (
                    <MessageCircle className="size-4 shrink-0" />
                  )}
                  <span className="min-w-0 flex-1 truncate font-medium">{channel.name}</span>
                  {channel.unread_count > 0 && (
                    <span
                      title={t("channels.unread", { count: channel.unread_count })}
                      data-testid={`unread-badge-${channel.name}`}
                      className="rounded-full bg-destructive px-1.5 py-0.5 text-[10px] leading-none font-bold text-destructive-foreground"
                    >
                      {channel.unread_count}
                    </span>
                  )}
                </button>
                <button
                  onClick={() => void leave(channel)}
                  title={t("channels.leave")}
                  aria-label={t("channels.leave")}
                  className="absolute top-1/2 right-1 -translate-y-1/2 rounded-md p-1 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-destructive"
                >
                  <X className="size-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
