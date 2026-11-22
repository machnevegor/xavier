import {
  IconChevronUp,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconMenu2,
} from "@tabler/icons-react";
import { Link, useLocation } from "@tanstack/react-router";
import { cn } from "cn";
import { type ReactNode, useEffect, useState } from "react";
import { NavList } from "@/components/app-shell/nav-list.tsx";
import { placeFor } from "@/components/app-shell/sections.ts";
import { ShellFooter } from "@/components/app-shell/shell-footer.tsx";
import { useSidebarCollapsed } from "@/components/app-shell/use-sidebar-collapsed.ts";
import { Logo } from "@/components/brand/logo.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet.tsx";
import { ApprovalsBadge } from "@/features/approvals/approvals-badge.tsx";
import { m } from "@/paraglide/messages.js";

/** Tablet and desktop navigation: a full sidebar, or an icon rail when collapsed. */
function Sidebar() {
  const [collapsed, toggle] = useSidebarCollapsed();
  return (
    <aside
      className={cn(
        "bg-sidebar sticky top-0 hidden h-dvh shrink-0 flex-col border-r px-2 py-3 transition-[width] duration-150 ease-out md:flex",
        collapsed ? "w-14" : "w-60",
      )}
    >
      <div className={cn("flex h-10 items-center", collapsed && "flex-col")}>
        {!collapsed && (
          <Link
            className="flex min-w-0 flex-1 items-center gap-2 truncate rounded-sm pl-2.5 text-base font-semibold tracking-tight"
            to="/"
          >
            <Logo className="text-primary size-5 shrink-0" />
            {m.nav_xavier()}
          </Link>
        )}
        <Button
          aria-expanded={!collapsed}
          aria-label={
            collapsed ? m.nav_expand_navigation() : m.nav_collapse_navigation()
          }
          className="text-muted-foreground size-10 shrink-0"
          onClick={toggle}
          size="icon"
          variant="ghost"
        >
          {collapsed ? (
            <IconLayoutSidebarLeftExpand />
          ) : (
            <IconLayoutSidebarLeftCollapse />
          )}
        </Button>
      </div>
      <NavList
        className="-mx-1 mt-3 flex-1 overflow-y-auto px-1 py-1"
        collapsed={collapsed}
      />
      <ShellFooter collapsed={collapsed} />
    </aside>
  );
}

/**
 * Phone navigation: a bar along the bottom edge names the current place and raises a sheet
 * with every destination, within thumb reach.
 */
function MobileNav() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const place = placeFor(pathname);
  const Icon = place?.icon ?? IconMenu2;
  const label = place?.label() ?? m.nav_menu();

  useEffect(() => {
    const media = window.matchMedia("(min-width: 768px)");
    const close = () => setOpen(false);
    media.addEventListener("change", close);
    return () => media.removeEventListener("change", close);
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <Sheet onOpenChange={setOpen} open={open}>
      <div className="bg-background sticky bottom-0 z-30 border-t pb-[env(safe-area-inset-bottom)] md:hidden">
        <SheetTrigger className="active:bg-accent flex h-12 w-full items-center gap-3 px-4 text-left">
          <Icon
            aria-hidden="true"
            className="size-5 shrink-0"
            stroke={place ? 2.25 : 1.75}
          />
          <span className="sr-only">
            {m.nav_open_navigation_current_section()}
          </span>
          <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
          <ApprovalsBadge />
          <IconChevronUp
            aria-hidden="true"
            className="text-muted-foreground size-5 shrink-0"
          />
        </SheetTrigger>
      </div>
      <SheetContent
        className="bg-sidebar max-h-[85dvh] gap-0 rounded-t-xl md:hidden [&_[data-slot=sheet-close]]:top-2"
        side="bottom"
      >
        <SheetHeader className="h-12 shrink-0 justify-center py-0">
          <SheetTitle className="flex items-center gap-2 text-base leading-none font-semibold tracking-tight">
            <Logo className="text-primary size-5" />
            {m.nav_xavier()}
          </SheetTitle>
          <SheetDescription className="sr-only">
            {m.nav_main_navigation()}
          </SheetDescription>
        </SheetHeader>
        <div className="overflow-y-auto overscroll-contain px-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <NavList onNavigate={() => setOpen(false)} touch />
          <ShellFooter onNavigate={() => setOpen(false)} touch />
        </div>
      </SheetContent>
    </Sheet>
  );
}

/**
 * `--shell-bottom` is the height pinned to the bottom edge on a phone, so anything sticking to
 * the bottom of the page (the message composer) sits above it.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="bg-background flex min-h-dvh [--shell-bottom:calc(3rem+1px+env(safe-area-inset-bottom))] md:[--shell-bottom:0px]">
      <a
        className="focus:bg-primary focus:text-primary-foreground sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:px-3 focus:py-2"
        href="#main"
      >
        {m.nav_skip_to_content()}
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col pt-[env(safe-area-inset-top)]">
        <main className="flex min-h-0 min-w-0 flex-1 flex-col" id="main">
          {children}
        </main>
        <MobileNav />
      </div>
    </div>
  );
}
