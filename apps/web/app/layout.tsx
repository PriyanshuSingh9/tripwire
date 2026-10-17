import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { AppShell } from "@/components/shell/app-shell";
import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "TraceAML Graph Console",
  description:
    "Graph-powered AML investigation workbench: ranked replay alerts, bounded FalkorDB evidence, deterministic policy and recorded benchmarks.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>): React.JSX.Element {
  return (
    <html lang="en" className={inter.variable}>
      <body className="antialiased">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
