import { Suspense } from "react";

import { SimulationPage } from "@/components/simulation/simulation-page";
import { PageFallback } from "@/components/shell/page-fallback";

export const metadata = { title: "Simulation · TraceAML" };

export default function Page(): React.JSX.Element {
  return (
    <Suspense fallback={<PageFallback label="Loading the run workspace" />}>
      <SimulationPage />
    </Suspense>
  );
}
