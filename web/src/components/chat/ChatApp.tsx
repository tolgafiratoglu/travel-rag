"use client";

import { useEffect, useState } from "react";

import { useChatStream } from "@/hooks/useChatStream";
import { readTosAccepted, writeTosAccepted } from "@/lib/tos";
import { ConversationScreen } from "./ConversationScreen";
import { LandingScreen } from "./LandingScreen";

export function ChatApp() {
  const [started, setStarted] = useState(false);
  const [input, setInput] = useState("");
  const [tosAccepted, setTosAccepted] = useState(false);
  const [tosReady, setTosReady] = useState(false);
  const { messages, loading, send } = useChatStream();

  useEffect(() => {
    setTosAccepted(readTosAccepted());
    setTosReady(true);
  }, []);

  function handleTosChange(accepted: boolean) {
    setTosAccepted(accepted);
    writeTosAccepted(accepted);
  }

  function sendMessage(text: string) {
    const q = text.trim();
    if (!q || loading || !tosAccepted) return;

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
          tosAccepted={tosAccepted}
          onTosChange={handleTosChange}
          disabled={loading || !tosReady}
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
        onBrandClick={() => {
          setStarted(false);
          setInput("");
        }}
        disabled={loading}
      />
    </div>
  );
}
