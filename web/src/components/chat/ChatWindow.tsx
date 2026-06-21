"use client";

import { useState } from "react";

import { postChat } from "@/lib/api";
import { ChatInput } from "./ChatInput";
import { MessageBubble } from "./MessageBubble";

type Message = {
  role: "user" | "assistant";
  content: string;
};

export function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);

    try {
      const { answer } = await postChat(text);
      setMessages((prev) => [...prev, { role: "assistant", content: answer }]);
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="overflow-hidden rounded-lg border bg-gray-100 shadow-sm">
      <div className="flex h-96 flex-col gap-3 overflow-y-auto p-4">
        {messages.length === 0 && !loading && (
          <p className="m-auto text-sm text-gray-400">
            Ask a travel question to get started.
          </p>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} role={msg.role} content={msg.content} />
        ))}
        {loading && (
          <p className="text-sm text-gray-400">Thinking...</p>
        )}
        {error && (
          <p className="text-sm text-red-600">{error}</p>
        )}
      </div>
      <ChatInput
        value={input}
        onChange={setInput}
        onSubmit={handleSend}
        disabled={loading}
      />
    </div>
  );
}
