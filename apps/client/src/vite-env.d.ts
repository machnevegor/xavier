interface ImportMetaEnv {
  readonly VITE_HYPERDX_API_KEY?: string;
  readonly VITE_HYPERDX_URL?: string;
  readonly VITE_SERVICE_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  __xavierTelemetryStarted?: boolean;
}
