import DatabaseClient from "@/components/pages/database-client";

/** Server Component page — avoids ClientPageRoot injecting async searchParams/params. */
export default function Page() {
  return <DatabaseClient />;
}
