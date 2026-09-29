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
  "Plan a 5-day trip to Istanbul focused on history, museums, and walkable districts.",
  "Compare Paris and Rome for museums, historical sites, and overall sightseeing.",
  "What historical day trips can I take from Antalya within 90 minutes?",
  "Plan 4 days in Porto and compare it with Seville for history and sightseeing.",
  "Which airports serve Lyon, and what should I know about getting into the city?",
  "I want to visit Barcelona.",
];
