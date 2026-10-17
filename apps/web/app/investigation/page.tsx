import { Suspense } from "react";

import { GuidedInvestigationPage } from "@/components/investigation/investigation-page";
import { PageFallback } from "@/components/shell/page-fallback";

export const metadata = { title: "Investigation · TraceAML" };

export default function Page(): React.JSX.Element {
  return (
    <Suspense fallback={<PageFallback label="Opening the investigation" />}>
      <GuidedInvestigationPage />
    </Suspense>
  );
}
