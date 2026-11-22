import { Link, useLocation } from "@tanstack/react-router";
import { cn } from "cn";
import { isActiveSection, SECTIONS } from "@/components/app-shell/sections.ts";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip.tsx";
import { ApprovalsBadge } from "@/features/approvals/approvals-badge.tsx";
import { m } from "@/paraglide/messages.js";

/** The same row the sidebar destinations use, including Account under the list. */
export function navItemClass({
  active,
  touch = false,
}: {
  active: boolean;
  touch?: boolean;
}): string {
  return cn(
    "active:bg-sidebar-accent relative flex items-center rounded-md transition-colors duration-100",
    touch ? "min-h-12 text-base" : "min-h-10",
    active
      ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
      : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
  );
}

/**
 * One list for every size. The icon sits in a fixed square, so it stays put whether the label
 * is shown (sidebar, sheet) or hidden (rail); a wider gap separates the groups.
 */
export function NavList({
  className,
  collapsed = false,
  touch = false,
  onNavigate,
}: {
  className?: string;
  collapsed?: boolean;
  touch?: boolean;
  onNavigate?: () => void;
}) {
  const { pathname } = useLocation();
  return (
    <nav aria-label={m.nav_main()} className={className}>
      <ul>
        {SECTIONS.map((section, index) => {
          const Icon = section.icon;
          const active = isActiveSection(section, pathname);
          const startsGroup =
            index > 0 && SECTIONS[index - 1]?.group !== section.group;
          const link = (
            <Link
              activeOptions={section.path === "/" ? { exact: true } : undefined}
              aria-current={active ? "page" : undefined}
              aria-label={collapsed ? section.label() : undefined}
              className={navItemClass({ active, touch })}
              onClick={onNavigate}
              title={collapsed ? undefined : section.hint()}
              to={section.path}
            >
              <span className="flex size-10 shrink-0 items-center justify-center">
                <Icon
                  aria-hidden="true"
                  className="size-5"
                  stroke={active ? 2.25 : 1.75}
                />
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate",
                  collapsed && "sr-only",
                )}
              >
                {section.label()}
              </span>
              {section.id === "approvals" && (
                <span
                  className={
                    collapsed
                      ? "bg-sidebar absolute top-0 right-0 rounded-full px-1 leading-4"
                      : "shrink-0 px-3"
                  }
                >
                  <ApprovalsBadge />
                </span>
              )}
            </Link>
          );
          return (
            <li className={startsGroup ? "mt-4" : "mt-0.5"} key={section.id}>
              {collapsed ? (
                <Tooltip>
                  <TooltipTrigger render={link} />
                  <TooltipContent side="right">
                    {section.label()}
                  </TooltipContent>
                </Tooltip>
              ) : (
                link
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
