import { StartClient } from "@tanstack/react-start/client";
import { StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { startTelemetry } from "@/lib/telemetry.ts";

startTelemetry();

hydrateRoot(
  document,
  <StrictMode>
    <StartClient />
  </StrictMode>,
);
