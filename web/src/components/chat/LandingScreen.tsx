import Link from "next/link";

import { ChatInput } from "./ChatInput";
import { SuggestionChips } from "./SuggestionChips";

type LandingScreenProps = {
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onSuggestionSelect: (text: string) => void;
  tosAccepted: boolean;
  onTosChange: (accepted: boolean) => void;
  disabled?: boolean;
};

export function LandingScreen({
  input,
  onInputChange,
  onSubmit,
  onSuggestionSelect,
  tosAccepted,
  onTosChange,
  disabled = false,
}: LandingScreenProps) {
  const blocked = disabled || !tosAccepted;

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
            submitDisabled={!tosAccepted}
          />
          <div className="mt-3 flex items-center justify-between gap-3 px-1">
            <label className="flex min-w-0 cursor-pointer items-center gap-2 text-left text-xs text-gray-600">
              <input
                type="checkbox"
                checked={tosAccepted}
                onChange={(e) => onTosChange(e.target.checked)}
                disabled={disabled}
                className="h-3.5 w-3.5 shrink-0 rounded border-gray-300"
              />
              <span>
                I agree to the{" "}
                <Link
                  href="/terms"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline underline-offset-2 hover:text-gray-900"
                  onClick={(e) => e.stopPropagation()}
                >
                  Terms of Service
                </Link>
              </span>
            </label>
            <p className="hidden shrink-0 text-right text-xs text-gray-400 sm:block">
              Powered by Qdrant, Neo4J, Qwen and BGE
            </p>
          </div>
        </div>
      </main>

      <footer className="shrink-0 px-4 pb-10 pt-4">
        <SuggestionChips onSelect={onSuggestionSelect} disabled={blocked} />
      </footer>
    </div>
  );
}
