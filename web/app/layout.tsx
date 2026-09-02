import type { Metadata } from "next";
import { Anton, Archivo, IBM_Plex_Mono } from "next/font/google";

import "./globals.css";
import { Rail } from "@/components/Rail";

const display = Anton({ subsets: ["latin"], weight: "400", variable: "--font-display" });
const body = Archivo({ subsets: ["latin"], variable: "--font-body" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "ShortsCreator — console de corte",
  description: "Geração automática de Shorts verticais 9:16 com QA de formato.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>
        <div className="shell">
          <Rail />
          <div className="main">{children}</div>
        </div>
      </body>
    </html>
  );
}
