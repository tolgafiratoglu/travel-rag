"use client";

import { useEffect, useRef } from "react";

import { MessageBubble } from "./MessageBubble";
import type { Message } from "./types";

type MessageListProps = {
  messages: Message[];
};

export function MessageList({ messages }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-1 px-4 py-6">
      {messages.map((msg, i) => (
        <MessageBubble key={i} role={msg.role} content={msg.content} status={msg.status} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
