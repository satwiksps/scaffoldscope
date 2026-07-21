import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { ReactNode } from "react";

import { siteConfig } from "@/lib/site";

import "./globals.css";

export const metadata: Metadata = {
  metadataBase: siteConfig.url,
  title: {
    default: "ScaffoldScope — Coding-agent harness ablations",
    template: "%s · ScaffoldScope",
  },
  description:
    "Hold the model, tasks, and budget constant. Compare coding-agent harness treatments with paired experiments and verifiable evidence.",
  applicationName: siteConfig.name,
  authors: [{ name: "ScaffoldScope contributors" }],
  creator: "ScaffoldScope contributors",
  keywords: [
    "coding agents",
    "AI agent evaluation",
    "agent harness",
    "context management",
    "SWE-bench",
    "ablation study",
    "reproducible evaluation",
  ],
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: "/",
    title: "ScaffoldScope — Controlled harness ablations",
    description:
      "Paired experiments and verifiable evidence for coding-agent harnesses.",
    siteName: siteConfig.name,
  },
  twitter: {
    card: "summary_large_image",
    title: "ScaffoldScope — Controlled harness ablations",
    description:
      "Paired experiments and verifiable evidence for coding-agent harnesses.",
  },
  category: "technology",
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#080a0e",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body
        className={[
          GeistSans.variable,
          GeistMono.variable,
          "min-h-screen bg-canvas font-sans antialiased",
        ].join(" ")}
      >
        {children}
      </body>
    </html>
  );
}
