import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { setupRouterSsrQueryIntegration } from "@tanstack/react-router-ssr-query";
import { getGlobalStartContext } from "@tanstack/react-start";
import { isUnauthorized } from "@/api/errors.ts";
import { retry } from "@/api/query.ts";
import { ErrorBoundary } from "@/components/fallbacks/error-boundary.tsx";
import { NotFound } from "@/components/fallbacks/not-found.tsx";
import { Pending } from "@/components/fallbacks/pending.tsx";
import { routeTree } from "./routeTree.gen.ts";

export function getRouter() {
  // An expired or revoked session ends here, whichever request notices it first.
  const onError = (error: unknown) => {
    const { pathname, href } = router.state.location;
    if (!isUnauthorized(error) || pathname === "/sign-in") {
      return;
    }
    queueMicrotask(() => queryClient.clear());
    void router.navigate({ search: { redirect: href }, to: "/sign-in" });
  };

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry, staleTime: 2000 } },
    mutationCache: new MutationCache({ onError }),
    queryCache: new QueryCache({ onError }),
  });

  const router = createRouter({
    context: { queryClient },
    defaultErrorComponent: ErrorBoundary,
    defaultNotFoundComponent: NotFound,
    defaultPendingComponent: Pending,
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
    routeTree,
    scrollRestoration: true,
    ssr: { nonce: getGlobalStartContext()?.nonce },
  });

  setupRouterSsrQueryIntegration({ queryClient, router });

  return router;
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>;
  }
}
