"use client";

import { ChatInput } from "./ChatInput";
import { MessageList } from "./MessageList";
import { SUGGESTIONS, type Message } from "./types";

type ConversationScreenProps = {
  messages: Message[];
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onSuggestionSelect?: (text: string) => void;
  onBrandClick?: () => void;
  disabled?: boolean;
};

export function ConversationScreen({
  messages,
  input,
  onInputChange,
  onSubmit,
  onSuggestionSelect,
  onBrandClick,
  disabled = false,
}: ConversationScreenProps) {
  function handleExampleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const value = e.target.value;
    e.target.value = "";
    if (!value || !onSuggestionSelect || disabled) return;
    onSuggestionSelect(value);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-gray-200 bg-white px-4 py-3">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4">
          <button
            type="button"
            className="text-left text-base font-semibold tracking-tight text-gray-900"
            aria-label="Visitor Atlas"
            onClick={onBrandClick}
          >
            Visitor Atlas
          </button>
          <select
            defaultValue=""
            onChange={handleExampleChange}
            disabled={disabled || !onSuggestionSelect}
            aria-label="Suggested queries"
            className="max-w-[min(100%,16rem)] rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700 outline-none hover:border-gray-300 focus:border-gray-300 focus:ring-1 focus:ring-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="" disabled>
              Suggested queries
            </option>
            {SUGGESTIONS.map((text) => (
              <option key={text} value={text}>
                {text}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <MessageList
          messages={messages}
          onSuggestionSelect={onSuggestionSelect}
          suggestionsDisabled={disabled}
        />
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
