import type { Metadata, Viewport } from "next";
import { Newsreader, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// The DS's two brand faces (readme "CAVEATS"): a serif (Newsreader) that
// carries the agent's spoken prose so answers read as typeset text, and
// JetBrains Mono as the concrete face for the mono "meta voice" (badges,
// tool names, counters). System sans stays native by design. next/font
// self-hosts these into the static export, so there's no runtime CDN call.
const serif = Newsreader({
  subsets: ["latin"],
  weight: ["400", "600"],
  style: ["normal", "italic"],
  variable: "--font-newsreader",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pilot",
  description: "Local AI computer agent",
  manifest: "/manifest.json",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "Pilot" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // Resize the layout (and thus 100dvh) when the on-screen keyboard opens,
  // so the composer stays pinned above the keyboard instead of being covered.
  interactiveWidget: "resizes-content",
  themeColor: "#0f172a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="sv" className={`${serif.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
