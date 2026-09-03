import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Loader2, Sparkles } from "lucide-react";
import { ApiError } from "@/lib/api";
import { describeError, useAuth } from "@/lib/auth";
import type { CopilotAnswer, CopilotUsage, DenialCode } from "@/lib/types";

const DENIAL_STYLES: Record<string, string> = {
  "deny:no-permission": "border-destructive/50 bg-destructive/10 text-destructive",
  "deny:out-of-scope": "border-border bg-muted text-muted-foreground",
  "deny:insufficient-context": "border-caution/50 bg-caution/10 text-caution",
  "infer:low-confidence": "border-warning/50 bg-warning/10 text-warning",
};

const DENIAL_KEYS: Record<string, string> = {
  "deny:no-permission": "copilot.deny_no_permission",
  "deny:out-of-scope": "copilot.deny_out_of_scope",
  "deny:insufficient-context": "copilot.deny_insufficient_context",
  "infer:low-confidence": "copilot.infer_low_confidence",
};

export function CopilotPanel({ contextKey }: { contextKey: string | null }) {
  const { t } = useTranslation();
  const { request } = useAuth();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [usage, setUsage] = useState<CopilotUsage | null>(null);
  const [usageOpen, setUsageOpen] = useState(false);

  useEffect(() => {
    setAnswer(null);
    setError(null);
    setQuestion("");
  }, [contextKey]);

  useEffect(() => {
    if (!usageOpen) return;
    void (async () => {
      try {
        const { data } = await request<CopilotUsage>("/api/v1/copilot/usage");
        setUsage(data);
      } catch {
        /* usage is optional */
      }
    })();
  }, [usageOpen, request]);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    const q = question.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await request<CopilotAnswer>("/api/v1/copilot/query", {
        method: "POST",
        body: JSON.stringify({ question: q, top_k: 8 }),
      });
      setAnswer(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setError(t("copilot.unavailable"));
      else setError(describeError(err, t));
    } finally {
      setLoading(false);
    }
  }

  const denial: DenialCode = answer?.denial_code ?? null;

  return (
    <aside className="flex min-h-0 w-full flex-col border-l border-border bg-sidebar">
      <header className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Sparkles className="size-4 text-primary" />
        <h2 className="font-display text-sm font-bold">{t("copilot.title")}</h2>
      </header>

      <form
        onSubmit={ask}
        data-testid="copilot-form"
        className="border-b border-border px-4 py-3"
      >
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={1000}
          rows={3}
          placeholder={t("copilot.ask_placeholder")}
          data-testid="copilot-input"
          className="w-full resize-none rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus:border-ring"
        />
        <button
          type="submit"
          disabled={loading}
          data-testid="copilot-submit"
          className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground disabled:opacity-60"
        >
          {loading && <Loader2 className="size-4 animate-spin" />}
          {t("copilot.ask")}
        </button>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 scroll-slim">
        {error && (
          <p className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        {!error && !answer && (
          <p className="text-sm text-muted-foreground">{t("copilot.empty")}</p>
        )}

        {denial && (
          <p
            data-testid={`copilot-banner-${denial}`}
            className={`mb-3 rounded-lg border px-3 py-2 text-xs ${DENIAL_STYLES[denial]}`}
          >
            {t(DENIAL_KEYS[denial] ?? "copilot.deny_out_of_scope")}
          </p>
        )}

        {answer && (
          <>
            {answer.text && (
              <p
                data-testid="copilot-answer"
                className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/90"
              >
                {answer.text}
              </p>
            )}

            {answer.citations.length > 0 && (
              <div className="mt-4" data-testid="copilot-citations">
                <h3 className="mb-2 text-[11px] font-bold tracking-widest text-muted-foreground uppercase">
                  {t("copilot.citations")}
                </h3>
                <ul className="space-y-1">
                  {answer.citations.map((citation) => (
                    <li key={citation.rw_id} data-testid={`citation-${citation.rw_id}`}>
                      <button
                        onClick={() => {
                          const el = document.getElementById(`message-${citation.rw_id}`);
                          el?.scrollIntoView({ behavior: "smooth", block: "center" });
                        }}
                        className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {citation.snippet}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="mt-4 text-[11px] text-muted-foreground">
              {t("copilot.confidence")}: {answer.confidence} · {t("copilot.prompt_version")}:{" "}
              {answer.prompt_version}
            </p>
          </>
        )}

        {loading && !answer && (
          <p
            data-testid="copilot-loading"
            className="text-sm text-muted-foreground"
          >
            {t("copilot.empty")}
          </p>
        )}
      </div>

      <div className="border-t border-border px-4 py-3">
        <button
          onClick={() => setUsageOpen((open) => !open)}
          className="flex w-full items-center justify-between text-[11px] font-bold tracking-widest text-muted-foreground uppercase"
        >
          {t("copilot.usage")}
          <ChevronDown
            className={`size-4 transition-transform ${usageOpen ? "rotate-180" : ""}`}
          />
        </button>
        {usageOpen && usage && (
          <dl className="mt-3 space-y-1 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <dt>{t("copilot.total_calls")}</dt>
              <dd className="text-foreground">{usage.total_calls}</dd>
            </div>
            <div className="flex justify-between">
              <dt>{t("copilot.total_tokens")}</dt>
              <dd className="text-foreground">
                {usage.total_prompt_tokens + usage.total_completion_tokens}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt>{t("copilot.total_cost")}</dt>
              <dd className="text-foreground">${usage.total_cost_usd.toFixed(4)}</dd>
            </div>
          </dl>
        )}
      </div>
    </aside>
  );
}
