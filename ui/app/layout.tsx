import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "PolyBot · Weather Trading",
  description: "Automated Kalshi weather market trading bot",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark h-full">
      <body className={`${inter.className} bg-[#0a0d14] text-slate-100 min-h-full antialiased`}>
        {children}
      </body>
    </html>
  );
}
