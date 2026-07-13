import type { Message, Source } from "./types";

type MessageBubbleProps = Pick<Message, "role" | "content" | "status" | "sources">;

function uniqueSources(sources: Source[]): Source[] {
  const seen = new Set<string>();
  const out: Source[] = [];
  for (const source of sources) {
    const key = source.source_url || `${source.city_name}:${source.section}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(source);
  }
  return out;
}

export function MessageBubble({ role, content, status, sources }: MessageBubbleProps) {
  const isUser = role === "user";
  const citationSources = sources ? uniqueSources(sources) : [];

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
      {citationSources.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-2">
          <p className="mb-1 text-xs font-medium text-gray-500">Kaynaklar</p>
          <ul className="space-y-1 text-xs">
            {citationSources.map((source) => (
              <li key={source.source_url || `${source.city_name}-${source.section}`}>
                {source.source_url ? (
                  <a
                    href={source.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                  >
                    {source.city_name}
                    {source.section ? ` — ${source.section}` : ""}
                  </a>
                ) : (
                  <span className="text-gray-600">
                    {source.city_name}
                    {source.section ? ` — ${source.section}` : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
