import type { Metadata } from "next";
import { Fredoka, JetBrains_Mono, Nunito } from "next/font/google";
import "./globals.css";
import GardenDecor from "@/components/GardenDecor";

const fredoka = Fredoka({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const nunito = Nunito({
  variable: "--font-garden",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "MindShift Lab",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${fredoka.variable} ${nunito.variable} ${jetbrainsMono.variable} antialiased`}>
      <body
        className="garden-app-root overflow-y-auto"
        style={{ fontFamily: "var(--font-garden), system-ui, sans-serif" }}
        suppressHydrationWarning
      >
        <GardenDecor />
        <div className="garden-content">{children}</div>
      </body>
    </html>
  );
}
