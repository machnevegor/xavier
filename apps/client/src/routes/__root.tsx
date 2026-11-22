import { TanStackDevtools } from "@tanstack/react-devtools";
import type { QueryClient } from "@tanstack/react-query";
import { ReactQueryDevtoolsPanel } from "@tanstack/react-query-devtools";
import {
  createRootRouteWithContext,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";
import { ThemeProvider } from "@/components/theme/theme-provider.tsx";
import { ThemeScript } from "@/components/theme/theme-script.tsx";
import { Toaster } from "@/components/ui/toast.tsx";
import { TooltipProvider } from "@/components/ui/tooltip.tsx";
import { getThemeState } from "@/lib/theme.ts";
import { m } from "@/paraglide/messages.js";
import { getLocale } from "@/paraglide/runtime.js";
import appCss from "@/styles/globals.css?url";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()(
  {
    head: () => ({
      links: [
        { href: appCss, rel: "stylesheet" },
        { href: "/favicon.svg", rel: "icon", type: "image/svg+xml" },
      ],
      meta: [
        { charSet: "utf-8" },
        {
          content: "width=device-width, initial-scale=1, viewport-fit=cover",
          name: "viewport",
        },
        {
          content: m.meta_supervise_an_agent_that_observes_runs_code_and(),
          name: "description",
        },
        { content: "noindex", name: "robots" },
        { title: m.meta_xavier() },
      ],
    }),
    shellComponent: RootDocument,
  },
);

function RootDocument({ children }: { children: React.ReactNode }) {
  const theme = getThemeState();

  return (
    <html
      className={theme.resolved === "dark" ? "dark" : undefined}
      lang={getLocale()}
      suppressHydrationWarning
    >
      <head>
        <HeadContent />
        <ThemeScript />
      </head>
      <body>
        <ThemeProvider initialIntent={theme.intent}>
          <TooltipProvider>
            <Toaster>{children}</Toaster>
          </TooltipProvider>
        </ThemeProvider>
        <TanStackDevtools
          config={{ position: "bottom-left" }}
          plugins={[
            {
              name: "TanStack Router",
              render: <TanStackRouterDevtoolsPanel />,
            },
            {
              name: "TanStack Query",
              render: <ReactQueryDevtoolsPanel />,
            },
          ]}
        />
        <Scripts />
      </body>
    </html>
  );
}
