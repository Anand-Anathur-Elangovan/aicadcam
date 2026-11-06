import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Manufacturing AI Intelligence",
  description: "Enterprise Manufacturing Multi-Modal RAG Chatbot System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full bg-gray-50 text-gray-900">{children}</body>
    </html>
  );
}
