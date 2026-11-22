import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { isUnauthorized } from "@/api/errors.ts";
import { AppShell } from "@/components/app-shell/app-shell.tsx";
import { sessionQuery } from "@/features/auth/api.ts";
import { syncProfileLocale } from "@/features/auth/locale.ts";

export const Route = createFileRoute("/_auth")({
  beforeLoad: async ({ context, location }) => {
    try {
      const me = await context.queryClient.ensureQueryData(sessionQuery());
      await syncProfileLocale(me, context.queryClient);
    } catch (error) {
      if (isUnauthorized(error)) {
        throw redirect({ search: { redirect: location.href }, to: "/sign-in" });
      }
      throw error;
    }
  },
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
  ssr: false,
});
