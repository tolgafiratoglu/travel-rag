import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Travel RAG",
  description: "AI-powered travel assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="h-dvh overflow-hidden">{children}</body>
    </html>
  );
}
