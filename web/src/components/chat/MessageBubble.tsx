import type { Message } from "./types";

type MessageBubbleProps = Pick<Message, "role" | "content" | "status">;

export function MessageBubble({ role, content, status }: MessageBubbleProps) {
  const isUser = role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end py-2">
        <div className="max-w-[85%] rounded-2xl bg-gray-100 px-4 py-2.5 text-sm whitespace-pre-wrap text-gray-900">
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="py-2">
      {status && (
        <p className="text-sm text-gray-400 animate-pulse">{status}</p>
      )}
      {content && (
        <div className="text-sm whitespace-pre-wrap text-gray-900">{content}</div>
      )}
    </div>
  );
}
