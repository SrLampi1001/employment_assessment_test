export type Locale = "es" | "en";

export interface Tokens {
  access_token: string;
  refresh_token: string;
  refresh_expires_at: string;
}

export interface CurrentUser {
  actor_id: string;
  username: string;
  display_name: string;
  locale: Locale;
}

export const CHANNEL_KIND_DIRECT = 1;
export const CHANNEL_KIND_GROUP = 2;

export const ROLE_MEMBER = 1;
export const ROLE_OWNER = 2;

export interface Channel {
  channel_id: string;
  name: string;
  kind: 1 | 2;
  created_by: string;
  created_at: string;
  my_role: 1 | 2;
  unread_count: number;
}

export interface ChannelListResponse {
  items: Channel[];
}

export interface UserSearchResult {
  rw_id: string;
  rw_username: string;
  rw_display_name: string;
  rw_locale: Locale;
}

export interface Message {
  rw_id: string;
  rw_channel_id: string;
  rw_author_id: string;
  rw_body: string;
  rw_is_edited: boolean;
  rw_created_at: string;
  rw_edited_at: string | null;
  is_mine: boolean;
}

export interface MessagePage {
  items: Message[];
  next_cursor_created_at: string | null;
  next_cursor_id: string | null;
}

export interface SearchHit {
  rw_id: string;
  rw_channel_id: string;
  rw_author_id: string;
  rw_body: string;
  rw_created_at: string;
  rw_highlight: string;
  is_mine: boolean;
}

export interface SearchResponse {
  items: SearchHit[];
}

export type PendingState = "pending" | "sent" | "failed";

export interface PendingMessage {
  client_ref: string;
  body: string;
  state: PendingState;
  created_at: string;
}

export interface Citation {
  rw_id: string;
  rw_channel_id: string;
  snippet: string;
}

export type DenialCode =
  | "deny:no-permission"
  | "deny:out-of-scope"
  | "deny:insufficient-context"
  | "infer:low-confidence"
  | null;

export interface CopilotAnswer {
  text: string;
  citations: Citation[];
  denial_code: DenialCode;
  confidence: "high" | "low";
  prompt_version: string;
}

export interface CopilotUsage {
  total_calls: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
}

export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
}
