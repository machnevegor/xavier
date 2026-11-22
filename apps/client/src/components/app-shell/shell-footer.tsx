import { IconLogout, IconUser } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { cn } from "cn";
import { explain } from "@/api/errors.ts";
import { navItemClass } from "@/components/app-shell/nav-list.tsx";
import { isAccountPath } from "@/components/app-shell/sections.ts";
import { ThemeToggle } from "@/components/theme/theme-toggle.tsx";
import { Button } from "@/components/ui/button.tsx";
import { toast } from "@/components/ui/toast.tsx";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip.tsx";
import { signOut } from "@/features/auth/api.ts";
import { m } from "@/paraglide/messages.js";

export function ShellFooter({
  collapsed = false,
  touch = false,
  onNavigate,
}: {
  collapsed?: boolean;
  touch?: boolean;
  onNavigate?: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const active = isAccountPath(pathname);
  const mutation = useMutation({
    mutationFn: signOut,
    onError: (error) =>
      toast.add({ title: explain(error).title, type: "error" }),
    onSuccess: async () => {
      queryClient.clear();
      await navigate({ search: { redirect: "/" }, to: "/sign-in" });
    },
  });
  const account = (
    <Link
      aria-label={collapsed ? m.nav_account() : undefined}
      className={navItemClass({ active, touch })}
      onClick={onNavigate}
      title={collapsed ? undefined : m.nav_sessions_and_api_keys()}
      to="/account"
    >
      <span className="flex size-10 shrink-0 items-center justify-center">
        <IconUser
          aria-hidden="true"
          className="size-5"
          stroke={active ? 2.25 : 1.75}
        />
      </span>
      <span className={cn("min-w-0 flex-1 truncate", collapsed && "sr-only")}>
        {m.nav_account()}
      </span>
    </Link>
  );

  return (
    <div
      className={
        collapsed ? "mt-3 flex flex-col items-center gap-1" : "mt-3 space-y-1"
      }
    >
      {collapsed ? (
        <Tooltip>
          <TooltipTrigger render={account} />
          <TooltipContent side="right">{m.nav_account()}</TooltipContent>
        </Tooltip>
      ) : (
        account
      )}
      <div
        className={
          collapsed
            ? "flex flex-col items-center gap-1 border-t pt-3"
            : "flex items-center gap-1 border-t pt-3"
        }
      >
        {!collapsed && <ThemeToggle />}
        <Button
          aria-label={m.nav_sign_out()}
          className={collapsed ? undefined : "ml-auto h-8 px-2"}
          disabled={mutation.isPending}
          onClick={() => mutation.mutate()}
          size={collapsed ? "icon" : "sm"}
          title={m.nav_sign_out()}
          variant="ghost"
        >
          <IconLogout />
          {!collapsed && m.nav_sign_out()}
        </Button>
      </div>
    </div>
  );
}
