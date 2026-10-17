import { Suspense } from "react";

import { CasesPage } from "@/components/cases/cases-page";
import { PageFallback } from "@/components/shell/page-fallback";

export const metadata = { title: "Cases · TraceAML" };

export default function Page(): React.JSX.Element {
  return (
    <Suspense fallback={<PageFallback label="Loading the case workspace" />}>
      <CasesPage />
    </Suspense>
  );
}
