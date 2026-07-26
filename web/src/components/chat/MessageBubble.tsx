import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message, Source } from "./types";

type MessageBubbleProps = Pick<Message, "role" | "content" | "status" | "sources">;

/**
 * Map [n] (and legacy [Sn]) → markdown link using sources[n-1].
 * Avoid matching words like [3 günlük].
 */
function linkifyCitations(text: string, sources: Source[]): string {
  return text.replace(/(?<![\w/])\[S?(\d+)\]/g, (match, num: string) => {
    const i = Number(num) - 1;
    const src = sources[i];
    if (!src) return match;
    if (src.source_url) {
      return `[[${num}]](${src.source_url})`;
    }
    return `[[${num}]](#src-${num})`;
  });
}

function siteName(source: Source): string {
  const raw = (source.source || "").toLowerCase();
  if (raw.includes("wikivoyage")) return "Wikivoyage";
  if (raw.includes("wikipedia") || raw === "wp") return "Wikipedia";
  if (raw === "neo4j" || raw === "graph") return "Travel knowledge graph";
  if (raw) return raw.charAt(0).toUpperCase() + raw.slice(1);
  if (source.source_url?.includes("wikivoyage")) return "Wikivoyage";
  if (source.source_url?.includes("wikipedia")) return "Wikipedia";
  return "Source";
}

function sectionLabel(section: string): string {
  if (!section || section === "graph") return "";
  const leaf = section.split("/").filter(Boolean).pop() || section;
  return leaf
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Chicago-style web citation (no author). */
function chicagoCitation(source: Source): { title: string; rest: string } {
  const title =
    source.name?.trim() ||
    source.city_name?.trim() ||
    "Untitled";
  const site = siteName(source);
  const sec = sectionLabel(source.section || "");
  const parts = [site];
  if (sec) parts.push(`sec. ${sec}`);
  return { title, rest: parts.join(", ") };
}

const markdownComponents: Components = {
  p: ({ children }) => <p className="mb-3 leading-relaxed last:mb-0">{children}</p>,
  strong: ({ children }) => (
    <strong className="font-semibold text-gray-900">{children}</strong>
  ),
  ul: ({ children }) => (
    <ul className="mb-3 list-disc space-y-2 pl-5 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-3 list-decimal space-y-2 pl-5 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => {
    const isHash = typeof href === "string" && href.startsWith("#");
    return (
      <a
        href={href}
        className="font-medium text-blue-600 no-underline hover:underline"
        {...(isHash
          ? {}
          : { target: "_blank", rel: "noopener noreferrer" })}
      >
        {children}
      </a>
    );
  },
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="border-b border-gray-200 bg-gray-50">{children}</thead>
  ),
  th: ({ children }) => <th className="px-3 py-2 font-semibold">{children}</th>,
  td: ({ children }) => (
    <td className="border-b border-gray-100 px-3 py-2 align-top">{children}</td>
  ),
};

export function MessageBubble({ role, content, status, sources }: MessageBubbleProps) {
  const isUser = role === "user";
  const citationSources = sources ?? [];
  const renderedContent =
    content && citationSources.length > 0
      ? linkifyCitations(content, citationSources)
      : content;

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
      {renderedContent && (
        <div className="text-sm text-gray-900">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {renderedContent}
          </ReactMarkdown>
        </div>
      )}
      {citationSources.length > 0 && (
        <div className="mt-4 border-t border-gray-100 pt-3">
          <p className="mb-2 text-xs font-medium tracking-wide text-gray-500 uppercase">
            References
          </p>
          <ol className="list-none space-y-2 text-xs leading-relaxed text-gray-700">
            {citationSources.map((source, i) => {
              const n = i + 1;
              const { title, rest } = chicagoCitation(source);
              const body = (
                <>
                  <span className="mr-1.5 tabular-nums text-gray-500">{n}.</span>
                  <span>
                    “{title}.” <em>{rest}</em>
                    {source.source_url ? "." : ""}
                  </span>
                </>
              );
              return (
                <li key={`src-${n}-${source.source_url || title}`} id={`src-${n}`}>
                  {source.source_url ? (
                    <a
                      href={source.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-gray-700 hover:text-blue-700"
                    >
                      {body}
                    </a>
                  ) : (
                    <span>{body}</span>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
