export type Source = {
  text: string;
  city_name: string;
  section: string;
  source_url: string;
  score: number;
};

export type ChatResponse = {
  answer: string;
  sources: Source[];
};

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function postChat(message: string): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  if (!res.ok) {
    throw new Error(`Chat request failed (${res.status})`);
  }

  return res.json();
}
