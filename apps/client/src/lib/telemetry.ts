import { useSyncExternalStore } from "react";

let sessionId: string | undefined;
const listeners = new Set<() => void>();

/**
 * Start browser telemetry when the build configures it.
 *
 * The SDK is loaded on demand, so it stays out of the initial bundle and a failure to load it
 * never affects the app. Session replay, console capture and traces use the HyperDX defaults;
 * typed input values are masked.
 */
export function startTelemetry(): void {
  const {
    MODE,
    VITE_HYPERDX_API_KEY: apiKey,
    VITE_HYPERDX_URL: url,
    VITE_SERVICE_NAME: service,
  } = import.meta.env;
  if (!apiKey || !url || !service) {
    return;
  }
  if (window.__xavierTelemetryStarted) {
    return;
  }
  window.__xavierTelemetryStarted = true;
  import("@hyperdx/browser")
    .then(({ default: HyperDX }) => {
      HyperDX.init({
        apiKey,
        url,
        service,
        tracePropagationTargets: [
          new RegExp(
            `^${window.location.origin.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}/api/`,
          ),
        ],
        maskAllInputs: true,
        otelResourceAttributes: { "deployment.environment.name": MODE },
      });
      sessionId = HyperDX.getSessionId();
      for (const listener of listeners) {
        listener();
      }
    })
    .catch((error: unknown) => {
      console.warn("Telemetry is off: the SDK failed to start.", error);
    });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The telemetry session id, to quote when reporting a problem; undefined when telemetry is off. */
export function useTelemetrySessionId(): string | undefined {
  return useSyncExternalStore(subscribe, () => sessionId);
}
