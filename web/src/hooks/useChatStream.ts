"use client";

import { useCallback, useRef, useState } from "react";

import { connectChat, type WsEvent } from "@/lib/ws";
import type { Message, Plan } from "@/components/chat/types";

export function useChatStream() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);

  const send = useCallback((question: string) => {
    const q = question.trim();
    if (!q || loading) return;

    const history = messages
      .filter(
        (m) =>
          (m.role === "user" || m.role === "assistant") &&
          (m.content || "").trim().length > 0,
      )
      .slice(-6)
      .map((m) => {
        const content = m.content.trim();
        const cut = content.search(/\n\n----- (?:prompt|planner response) -----\n/i);
        return {
          role: m.role,
          content: cut >= 0 ? content.slice(0, cut).trim() : content,
        };
      });

    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setLoading(true);

    let plan: Plan | undefined;

    const cleanup = connectChat(
      q,
      (event: WsEvent) => {
        switch (event.type) {
          case "status":
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: "", status: event.message },
            ]);
            break;

          case "plan":
            plan = {
              summary: event.summary,
              steps: event.steps.map((s) => ({
                id: s.id,
                label: s.label,
                kind: s.kind,
                status: s.status as Plan["steps"][number]["status"],
              })),
            };
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = { ...last, plan, status: event.summary };
              }
              return updated;
            });
            break;

          case "step": {
            if (!plan) break;
            plan = {
              ...plan,
              steps: plan.steps.map((s) =>
                s.kind === event.kind ? { ...s, status: event.status as Plan["steps"][number]["status"] } : s,
              ),
            };
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = { ...last, plan };
              }
              return updated;
            });
            break;
          }

          case "token":
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  content: (last.content || "") + event.delta,
                  status: undefined,
                };
              }
              return updated;
            });
            break;

          case "done":
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  content: event.answer || last.content,
                  plan: plan ?? last.plan,
                  status: undefined,
                  sources: event.sources ?? [],
                  suggestions: event.suggestions?.length
                    ? event.suggestions
                    : undefined,
                };
              }
              return updated;
            });
            break;

          case "error":
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  content: `Error: ${event.message}`,
                  status: undefined,
                };
              } else {
                updated.push({ role: "assistant", content: `Error: ${event.message}` });
              }
              return updated;
            });
            break;
        }
      },
      () => {
        setLoading(false);
        cleanupRef.current = null;
      },
      history,
    );

    cleanupRef.current = cleanup;
  }, [loading, messages]);

  return { messages, loading, send };
}
