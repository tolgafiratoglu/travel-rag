import { ChatWindow } from "@/components/chat/ChatWindow";

export default function HomePage() {
  return (
    <main className="container mx-auto max-w-3xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-semibold">Travel RAG</h1>
      <ChatWindow />
    </main>
  );
}
