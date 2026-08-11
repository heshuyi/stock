import HomeClient from "@/components/pages/home-client";

/** Server Component page — avoids ClientPageRoot injecting async searchParams/params. */
export default function Page() {
  return <HomeClient />;
}
