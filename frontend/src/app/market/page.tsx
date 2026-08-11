import MarketClient from "@/components/pages/market-client";

/** Server Component page — avoids ClientPageRoot injecting async searchParams/params. */
export default function Page() {
  return <MarketClient />;
}
