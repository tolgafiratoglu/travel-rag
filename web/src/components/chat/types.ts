export type PlanStep = {
  id: string;
  label: string;
  kind: string;
  status: "idle" | "running" | "done" | "error";
};

export type Plan = {
  summary: string;
  steps: PlanStep[];
};

export type Message = {
  role: "user" | "assistant";
  content: string;
  plan?: Plan;
  status?: string;
};

export const SUGGESTIONS = [
  "Antalya'da aileyle gidilecek yerler neresi?",
  "İstanbul'da 2 günlük rota öner",
  "Kapadokya için en iyi mevsim ne zaman?",
  "Bodrum'da bütçe dostu aktiviteler",
];
