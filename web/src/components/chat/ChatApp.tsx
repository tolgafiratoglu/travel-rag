"use client";

import { useState } from "react";

import { useChatStream } from "@/hooks/useChatStream";
import { ConversationScreen } from "./ConversationScreen";
import { LandingScreen } from "./LandingScreen";

export function ChatApp() {
  const [started, setStarted] = useState(false);
  const [input, setInput] = useState("");
  const { messages, loading, send } = useChatStream();

  function sendMessage(text: string) {
    const q = text.trim();
    if (!q || loading) return;

    setStarted(true);
    setInput("");
    send(q);
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
        onSuggestionSelect={handleSuggestionSelect}
        disabled={loading}
      />
    </div>
  );
}
