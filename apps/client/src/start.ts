import { createMiddleware, createStart } from "@tanstack/react-start";

const TELEMETRY_URL = import.meta.env.VITE_HYPERDX_URL;

function contentSecurityPolicy(nonce: string): string {
  const connect = TELEMETRY_URL ? ` ${new URL(TELEMETRY_URL).origin}` : "";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    "style-src 'self' 'unsafe-inline'",
    // noVNC draws JPEG rectangles of the screen through data: images; picked images are
    // previewed from blob: URLs before they are uploaded.
    "img-src 'self' data: blob:",
    `connect-src 'self'${connect}`,
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/**
 * Every document gets a fresh nonce: the router stamps it on the inline scripts of the shell,
 * and the policy admits only those. The dev server injects its own scripts, so it gets none.
 */
const securityHeaders = createMiddleware().server(async ({ next }) => {
  const nonce = btoa(
    String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))),
  );
  const result = await next({ context: { nonce } });
  if (import.meta.env.PROD) {
    result.response.headers.set(
      "content-security-policy",
      contentSecurityPolicy(nonce),
    );
    result.response.headers.set("x-content-type-options", "nosniff");
  }
  return result;
});

export const startInstance = createStart(() => ({
  requestMiddleware: [securityHeaders],
}));
