import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Terms of Service — Visitor Atlas",
  description: "Terms of Service for Visitor Atlas",
};

export default function TermsPage() {
  return (
    <main className="h-dvh overflow-y-auto px-4 py-12 text-sm leading-relaxed text-gray-700">
      <div className="mx-auto max-w-2xl">
        <Link href="/" className="text-gray-500 hover:text-gray-800">
          ← Visitor Atlas
        </Link>
        <h1 className="mt-6 text-2xl font-semibold text-gray-900">
          Terms of Service
        </h1>
        <p className="mt-4 text-gray-500">Last updated: August 2026</p>

        <section className="mt-8 space-y-4 pb-12">
          <p>
            Visitor Atlas is an AI-powered travel assistant. By using the service,
            you agree to these terms.
          </p>
          <p>
            Answers may be generated with third-party AI providers (including
            Google Gemini and local Qwen models). Embeddings and retrieval use
            Qdrant; graph lookups may use Neo4j. Do not submit sensitive personal
            data, passwords, or illegal requests. Content is informational and may
            be incomplete or inaccurate; verify critical travel details
            independently.
          </p>
          <p>
            We may update these terms. Continued use after changes constitutes
            acceptance of the revised terms.
          </p>
          <p className="text-gray-500">
            This page is a placeholder summary, not a complete legal agreement.
            Replace with counsel-reviewed terms before production launch.
          </p>
        </section>
      </div>
    </main>
  );
}
