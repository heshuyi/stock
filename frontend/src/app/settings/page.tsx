import SettingsClient from "@/components/pages/settings-client";

/** Server Component page — avoids ClientPageRoot injecting async searchParams/params. */
export default function Page() {
  return <SettingsClient />;
}
