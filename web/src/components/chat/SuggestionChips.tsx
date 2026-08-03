import { SUGGESTIONS } from "./types";

type SuggestionChipsProps = {
  onSelect: (text: string) => void;
  suggestions?: string[];
  disabled?: boolean;
};

export function SuggestionChips({
  onSelect,
  suggestions = SUGGESTIONS,
  disabled = false,
}: SuggestionChipsProps) {
  if (!suggestions.length) return null;

  return (
    <div className="mx-auto grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
      {suggestions.map((text) => (
        <button
          key={text}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(text)}
          className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-left text-sm text-gray-700 shadow-sm transition hover:bg-gray-50 hover:border-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {text}
        </button>
      ))}
    </div>
  );
}
