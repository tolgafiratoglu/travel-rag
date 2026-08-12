const STRINGS = {
  thinking: { en: "Thinking…", tr: "Düşünüyor…" },
  answering: { en: "Answering your question…", tr: "Sorunuzu yanıtlıyorum…" },
} as const;

export type UiLang = keyof typeof STRINGS.thinking;

export function normalizeUiLang(code?: string | null): UiLang {
  const base = (code || "en").trim().toLowerCase().split("-")[0];
  return base === "tr" ? "tr" : "en";
}

export function uiString(
  key: keyof typeof STRINGS,
  lang?: string | null,
): string {
  const l = normalizeUiLang(lang);
  return STRINGS[key][l];
}
