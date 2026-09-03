import type { Metadata, Viewport } from "next";
import { Anton, Archivo, IBM_Plex_Mono } from "next/font/google";

import "./globals.css";
import { Shell } from "@/components/Shell";
import { I18nProvider } from "@/lib/i18n";

const display = Anton({ subsets: ["latin"], weight: "400", variable: "--font-display" });
const body = Archivo({ subsets: ["latin"], variable: "--font-body" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "ShortsCreator",
  description:
    "Automatic 9:16 vertical shorts with narration, synced karaoke captions and format QA.",
  applicationName: "ShortsCreator",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "ShortsCreator" },
  formatDetection: { telephone: false },
};

// viewport-fit=cover is what unlocks env(safe-area-inset-*) on notched iPhones
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#08090a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>
        <I18nProvider>
          <Shell>{children}</Shell>
        </I18nProvider>
      </body>
    </html>
  );
}
