"use client";

import { useState } from "react";

import { ConversationScreen } from "./ConversationScreen";
import { LandingScreen } from "./LandingScreen";
import type { Message } from "./types";

export function ChatApp() {
  const [started, setStarted] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  function sendMessage(text: string) {
    const q = text.trim();
    if (!q || loading) return;

    setStarted(true);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setLoading(true);

    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Simüle cevap: "${q}" hakkında bilgi toplanacak.`,
        },
      ]);
      setLoading(false);
    }, 600);
  }

  function handleSubmit() {
    sendMessage(input);
  }

  function handleSuggestionSelect(text: string) {
    sendMessage(text);
  }

  if (!started) {
    return (
      <div className="h-dvh">
        <LandingScreen
          input={input}
          onInputChange={setInput}
          onSubmit={handleSubmit}
          onSuggestionSelect={handleSuggestionSelect}
          disabled={loading}
        />
      </div>
    );
  }

  return (
    <div className="h-dvh">
      <ConversationScreen
        messages={messages}
        input={input}
        onInputChange={setInput}
        onSubmit={handleSubmit}
        disabled={loading}
      />
    </div>
  );
}
