export type WsEvent =
  | { type: "status"; message: string }
  | { type: "plan"; summary: string; steps: { id: string; label: string; kind: string; status: string }[]; prompt_language?: string }
  | { type: "step"; kind: string; status: string; detail?: string; status_message?: string; step_id?: string }
  | { type: "token"; delta: string }
  | {
      type: "done";
      answer: string;
      sources: {
        text: string;
        city_name: string;
        section: string;
        source_url: string;
        score: number;
        source?: string;
        name?: string;
      }[];
      plan: Record<string, unknown>;
      primary_intent?: string;
      in_scope?: boolean;
      needs_clarification?: boolean;
      suggestions?: string[];
      intents?: { intent: string; cities: string[] }[];
      prompt_language?: string;
      search_queries?: { q: string; city?: string | null }[];
      deep_search?: boolean;
      search_museums?: boolean;
      search_artifacts?: boolean;
      museum_search_queries?: { q: string; city?: string | null }[];
      artifact_search_queries?: { q: string; city?: string | null }[];
      is_hotel_search?: boolean;
      hotel_search_query?: string;
      is_flight_search?: boolean;
      flight_search_query?: string;
      needs_graph?: boolean;
      graph_queries?: { template: string; params?: Record<string, unknown> }[];
    }
  | { type: "error"; message: string };

const WS_BASE =
  (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000")
    .replace(/^http/, "ws");

export function connectChat(
  question: string,
  onEvent: (event: WsEvent) => void,
  onClose?: () => void,
  history: { role: string; content: string }[] = [],
): () => void {
  const ws = new WebSocket(`${WS_BASE}/ws/chat`);

  ws.onopen = () => {
    ws.send(
      JSON.stringify({
        type: "message",
        content: question,
        history,
      }),
    );
  };

  ws.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data) as WsEvent;
      onEvent(event);
    } catch {
      /* ignore malformed frames */
    }
  };

  ws.onerror = () => {
    onEvent({ type: "error", message: "WebSocket connection failed" });
  };

  ws.onclose = () => {
    onClose?.();
  };

  return () => {
    ws.close();
  };
}
