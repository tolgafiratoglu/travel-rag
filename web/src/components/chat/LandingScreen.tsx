import { ChatInput } from "./ChatInput";
import { SuggestionChips } from "./SuggestionChips";

type LandingScreenProps = {
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onSuggestionSelect: (text: string) => void;
  disabled?: boolean;
};

export function LandingScreen({
  input,
  onInputChange,
  onSubmit,
  onSuggestionSelect,
  disabled = false,
}: LandingScreenProps) {
  return (
    <div className="flex h-full flex-col">
      <header className="shrink-0 px-4 pt-16 text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-gray-900">
          Visitor Atlas
        </h1>
        <p className="mt-2 text-sm text-gray-500">
          AI-powered travel assistant for your next adventure
        </p>
      </header>

      <main className="flex flex-1 items-center justify-center px-4">
        <div className="w-full max-w-2xl">
          <ChatInput
            value={input}
            onChange={onInputChange}
            onSubmit={onSubmit}
            disabled={disabled}
          />
        </div>
      </main>

      <footer className="shrink-0 px-4 pb-10 pt-4">
        <SuggestionChips onSelect={onSuggestionSelect} disabled={disabled} />
      </footer>
    </div>
  );
}
