import type { Metadata, Viewport } from "next";
import { DesktopNav, MobileBottomNav } from "@/components/site-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "A股宽基定投投顾看板",
  description: "A股宽基分角色差异化定投、空头排列过滤与追踪止盈",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=Source+Sans+3:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen">
        <div className="mx-auto max-w-6xl px-4 pb-24 pt-6 sm:px-6 sm:pb-16 sm:pt-8">
          <header className="mb-6 sm:mb-10">
            <p className="font-display text-2xl tracking-tight text-ink sm:text-5xl">
              宽基定投投顾
            </p>
            <p className="mt-1 line-clamp-2 max-w-2xl text-sm text-ink/70 sm:mt-2 sm:text-base">
              华泰柏瑞沪深300ETF（510300）等五只宽基 · 分角色定投 · 硬否决可暂停
            </p>
            <DesktopNav />
          </header>
          {children}
          <footer className="mt-10 border-t border-ink/10 pt-4 text-xs text-ink/50 sm:mt-12">
            策略信号仅供学习与个人研究参考，不构成投资建议。
          </footer>
        </div>
        <MobileBottomNav />
      </body>
    </html>
  );
}
