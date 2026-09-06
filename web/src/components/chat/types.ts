export type PlanStep = {
  id: string;
  label: string;
  kind: string;
  status: "idle" | "running" | "done" | "error";
  detail?: string;
};

export type Plan = {
  summary: string;
  steps: PlanStep[];
};

export type Source = {
  text: string;
  city_name: string;
  section: string;
  source_url: string;
  score: number;
  source?: string;
  name?: string;
  title?: string;
};

export type Message = {
  role: "user" | "assistant";
  content: string;
  plan?: Plan;
  status?: string;
  sources?: Source[];
  suggestions?: string[];
};

export const SUGGESTIONS = [
  "Plan a 3-day trip to Istanbul focused on history, museums, and walkable areas.",
  "Compare Madrid and Istanbul for museums, historical sites, and overall sightseeing.",
  "What historical day trips can I take from Antalya within 90 minutes?",
  "Plan 4 days in Antalya and compare it with Bodrum for history and sightseeing.",
  "Which airports serve Istanbul, and what should I know about getting into the city?",
  "I want to visit Barcelona.",
];
