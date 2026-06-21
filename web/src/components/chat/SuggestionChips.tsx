import { SUGGESTIONS } from "./types";

type SuggestionChipsProps = {
  onSelect: (text: string) => void;
};

export function SuggestionChips({ onSelect }: SuggestionChipsProps) {
  return (
    <div className="mx-auto grid w-full max-w-2xl grid-cols-1 gap-2 px-4 sm:grid-cols-2">
      {SUGGESTIONS.map((text) => (
        <button
          key={text}
          type="button"
          onClick={() => onSelect(text)}
          className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-left text-sm text-gray-700 shadow-sm transition hover:bg-gray-50 hover:border-gray-300"
        >
          {text}
        </button>
      ))}
    </div>
  );
}
