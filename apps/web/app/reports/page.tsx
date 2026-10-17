import { Suspense } from "react";

import { ReportsPage } from "@/components/reports/reports-page";
import { PageFallback } from "@/components/shell/page-fallback";

export const metadata = { title: "Reports & System · TraceAML" };

export default function Page(): React.JSX.Element {
  return (
    <Suspense fallback={<PageFallback label="Loading reports and system status" />}>
      <ReportsPage />
    </Suspense>
  );
}
