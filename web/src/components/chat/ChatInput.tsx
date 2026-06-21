type ChatInputProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
};

export function ChatInput({
  value,
  onChange,
  onSubmit,
  disabled = false,
}: ChatInputProps) {
  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  }

  return (
    <div className="flex items-end gap-2 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm focus-within:border-gray-300 focus-within:ring-1 focus-within:ring-gray-200">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder="Ask about travel..."
        rows={1}
        className="max-h-40 min-h-[24px] flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-gray-400 disabled:opacity-50"
      />
      <button
        type="button"
        onClick={onSubmit}
        disabled={disabled || !value.trim()}
        aria-label="Send message"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-white transition hover:bg-gray-700 disabled:cursor-not-allowed disabled:bg-gray-300"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="currentColor"
          className="h-4 w-4"
        >
          <path d="M3.478 2.404a.75.75 0 0 0-.926.941l2.432 7.905H13.5a.75.75 0 0 1 0 1.5H4.984l-2.432 7.905a.75.75 0 0 0 .926.94 60.519 60.519 0 0 0 18.445-8.986.75.75 0 0 0 0-1.218A60.517 60.517 0 0 0 3.478 2.404Z" />
        </svg>
      </button>
    </div>
  );
}
