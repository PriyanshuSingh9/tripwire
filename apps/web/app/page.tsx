import { Suspense } from "react";

import { OverviewPage } from "@/components/overview/overview-page";
import { PageFallback } from "@/components/shell/page-fallback";

export const metadata = { title: "Overview · TraceAML" };

export default function Page(): React.JSX.Element {
  return (
    <Suspense fallback={<PageFallback label="Loading the command centre" />}>
      <OverviewPage />
    </Suspense>
  );
}
