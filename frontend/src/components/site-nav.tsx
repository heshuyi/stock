"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const nav = [
  { href: "/", label: "今日", full: "今日操作" },
  { href: "/strategies", label: "策略", full: "策略对比" },
  { href: "/review", label: "复盘", full: "信号复盘" },
  { href: "/market", label: "行情", full: "标的详情" },
  { href: "/database", label: "数据", full: "数据库" },
  { href: "/settings", label: "设置", full: "持仓与设置" },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function DesktopNav() {
  const pathname = usePathname();
  return (
    <nav className="mt-6 hidden flex-wrap gap-2 border-b border-ink/10 pb-4 sm:flex">
      {nav.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`min-h-11 rounded-md px-3 py-2 text-sm font-medium transition ${
              active
                ? "bg-ink text-paper"
                : "text-ink/80 hover:bg-ink/5 hover:text-ink"
            }`}
          >
            {item.full}
          </Link>
        );
      })}
    </nav>
  );
}

export function MobileBottomNav() {
  const pathname = usePathname();
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-40 border-t border-ink/10 bg-paper/95 backdrop-blur sm:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
      aria-label="主导航"
    >
      <ul className="mx-auto grid max-w-6xl grid-cols-6">
        {nav.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`flex min-h-12 flex-col items-center justify-center px-1 text-[11px] font-semibold tracking-wide ${
                  active ? "text-moss" : "text-ink/55"
                }`}
              >
                <span
                  className={`mb-0.5 h-1 w-4 rounded-full ${
                    active ? "bg-moss" : "bg-transparent"
                  }`}
                  aria-hidden
                />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
