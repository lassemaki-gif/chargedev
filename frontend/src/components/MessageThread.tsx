"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, MessageOut } from "@/lib/api";
import { pickUIT } from "@/lib/ui";

interface Props {
  bookingId: number;
  // The current viewer's role in this thread, so the component knows which
  // side to render each message on without comparing user ids.
  viewerRole: "buyer" | "host";
  // The counterparty's display name — used only for the input placeholder.
  counterpartyName?: string;
}

// Polling cadence when the panel is open. Lightweight: three GETs per minute
// on an unlisted page is fine for a pilot and avoids websocket plumbing.
const POLL_MS = 20_000;

export function MessageThread({ bookingId, viewerRole, counterpartyName }: Props) {
  const search = useSearchParams();
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT(uiLang), [uiLang]);

  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await api.listMessages(bookingId);
      setMessages(rows);
      setLoaded(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not load messages.");
    }
  }, [bookingId]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  async function send() {
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true);
    setError(null);
    try {
      const m = await api.sendMessage(bookingId, body);
      setMessages((prev) => [...prev, m]);
      setDraft("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Message failed.");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="border border-border rounded-lg bg-card p-3 flex flex-col gap-3">
      <div className="max-h-72 overflow-y-auto flex flex-col gap-2 px-1">
        {!loaded ? (
          <p className="text-ash text-xs">…</p>
        ) : messages.length === 0 ? (
          <p className="text-ash text-xs">{t.threadEmpty}</p>
        ) : (
          messages.map((m) => {
            const mine =
              (viewerRole === "buyer" && m.sender_role === "buyer") ||
              (viewerRole === "host" && m.sender_role === "host");
            return (
              <div
                key={m.id}
                className={`flex ${mine ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words ${
                    mine
                      ? "bg-volt/20 text-white"
                      : m.sender_role === "admin"
                      ? "bg-yellow-900/30 text-yellow-200 border border-yellow-800/60"
                      : "bg-border text-white"
                  }`}
                >
                  <div className="text-ash text-[10px] uppercase tracking-wide mb-0.5">
                    {m.sender_role === "admin"
                      ? "ChargedEV support"
                      : m.sender_name ?? m.sender_role}
                    {" · "}
                    {new Date(m.created_at).toLocaleString()}
                  </div>
                  {m.body}
                </div>
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>
      <div className="flex gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={counterpartyName ? `${t.threadWritePlaceholder.replace("…", "")} ${counterpartyName}…` : t.threadWritePlaceholder}
          rows={2}
          maxLength={2000}
          className="input flex-1 text-sm"
        />
        <button
          onClick={send}
          disabled={sending || !draft.trim()}
          className="btn-volt text-sm px-4 self-end disabled:opacity-40"
        >
          {sending ? t.threadSending : t.threadSend}
        </button>
      </div>
      {error && <p className="text-red-400 text-xs">{error}</p>}
      <p className="text-ash text-[10px]">{t.threadSupportNote}</p>
    </div>
  );
}
