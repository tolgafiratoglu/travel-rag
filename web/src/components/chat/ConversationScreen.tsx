import { ChatInput } from "./ChatInput";
import { MessageList } from "./MessageList";
import type { Message } from "./types";

type ConversationScreenProps = {
  messages: Message[];
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
};

export function ConversationScreen({
  messages,
  input,
  onInputChange,
  onSubmit,
  disabled = false,
}: ConversationScreenProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <MessageList messages={messages} />
      </div>
      <div className="shrink-0 border-t border-gray-200 bg-gray-50/80 px-4 py-4 backdrop-blur">
        <div className="mx-auto w-full max-w-3xl">
          <ChatInput
            value={input}
            onChange={onInputChange}
            onSubmit={onSubmit}
            disabled={disabled}
          />
        </div>
      </div>
    </div>
  );
}
