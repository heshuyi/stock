import StrategiesClient from "@/components/pages/strategies-client";

/** Server Component page — avoids ClientPageRoot injecting async searchParams/params. */
export default function Page() {
  return <StrategiesClient />;
}
