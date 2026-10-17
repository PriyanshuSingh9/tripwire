import { Suspense } from "react";

import { AlertsPage } from "@/components/alerts/alerts-page";
import { PageFallback } from "@/components/shell/page-fallback";

export const metadata = { title: "Alerts · TraceAML" };

export default function Page(): React.JSX.Element {
  return (
    <Suspense fallback={<PageFallback label="Loading the alert queue" />}>
      <AlertsPage />
    </Suspense>
  );
}
