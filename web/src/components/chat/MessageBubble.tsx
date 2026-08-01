"use client";

import type { Components } from "react-markdown";
import { useId, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message, Source } from "./types";

type MessageBubbleProps = Pick<Message, "role" | "content" | "status" | "sources">;

const FLASH_MS = 18_000;

/**
 * Map [n] (and legacy [Sn]) → in-page link to References [#…-src-n].
 * Avoid matching words like [3 günlük].
 */
function linkifyCitations(text: string, sources: Source[], anchorPrefix: string): string {
  return text.replace(/(?<![\w/])\[S?(\d+)\]/g, (match, num: string) => {
    const i = Number(num) - 1;
    if (!sources[i]) return match;
    return `[[${num}]](#${anchorPrefix}-src-${num})`;
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

function flashReference(el: HTMLElement) {
  el.classList.remove("cite-flash");
  // Restart CSS animation when the same ref is clicked again.
  void el.offsetWidth;
  el.classList.add("cite-flash");
  window.setTimeout(() => {
    el.classList.remove("cite-flash");
  }, FLASH_MS);
}

function scrollToReference(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  flashReference(el);
}

export function MessageBubble({ role, content, status, sources }: MessageBubbleProps) {
  const isUser = role === "user";
  const reactId = useId();
  // useId includes colons; strip for valid HTML id / hash href.
  const anchorPrefix = `m${reactId.replace(/:/g, "")}`;
  const citationSources = sources ?? [];

  const renderedContent =
    content && citationSources.length > 0
      ? linkifyCitations(content, citationSources, anchorPrefix)
      : content;

  const markdownComponents = useMemo<Components>(
    () => ({
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
        const isCiteHash =
          typeof href === "string" &&
          href.startsWith(`#${anchorPrefix}-src-`);
        if (isCiteHash) {
          const targetId = href.slice(1);
          return (
            <a
              href={href}
              className="font-medium text-blue-600 no-underline hover:underline"
              onClick={(e) => {
                e.preventDefault();
                scrollToReference(targetId);
              }}
            >
              {children}
            </a>
          );
        }
        const isHash = typeof href === "string" && href.startsWith("#");
        return (
          <a
            href={href}
            className="font-medium text-blue-600 no-underline hover:underline"
            {...(isHash ? {} : { target: "_blank", rel: "noopener noreferrer" })}
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
    }),
    [anchorPrefix],
  );

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
              const id = `${anchorPrefix}-src-${n}`;
              return (
                <li
                  key={`${id}-${source.source_url || title}`}
                  id={id}
                  className="rounded px-1.5 py-1 -mx-1.5"
                >
                  <span className="mr-1.5 tabular-nums text-gray-500">{n}.</span>
                  <span>
                    “{title}.” <em>{rest}</em>.
                  </span>
                  {source.source_url ? (
                    <>
                      {" "}
                      <a
                        href={source.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="break-all text-blue-600 hover:underline"
                      >
                        {source.source_url}
                      </a>
                    </>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
