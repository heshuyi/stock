import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "A股宽基定投投顾看板",
  description: "A股宽基每周估值定投、趋势过滤与分批止盈",
};

const nav = [
  { href: "/", label: "今日操作" },
  { href: "/strategies", label: "策略对比" },
  { href: "/market", label: "标的详情" },
  { href: "/database", label: "数据库" },
  { href: "/settings", label: "持仓与设置" },
];

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
      <body className="min-h-screen antialiased">
        <div className="mx-auto max-w-6xl px-4 pb-16 pt-8 sm:px-6">
          <header className="mb-10">
            <p className="font-display text-4xl tracking-tight text-ink sm:text-5xl">
              宽基定投投顾
            </p>
            <p className="mt-2 max-w-2xl text-base text-ink/70">
              沪深300 / 中证500 / 创业板200 / 科创50 / 上证50 · 每周定投 · 每日止盈检查
            </p>
            <nav className="mt-6 flex flex-wrap gap-2 border-b border-ink/10 pb-4">
              {nav.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-md px-3 py-1.5 text-sm font-medium text-ink/80 transition hover:bg-ink/5 hover:text-ink"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </header>
          {children}
          <footer className="mt-12 border-t border-ink/10 pt-4 text-xs text-ink/50">
            策略信号仅供学习与个人研究参考，不构成投资建议。
          </footer>
        </div>
      </body>
    </html>
  );
}
