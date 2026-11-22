import {
  IconBellRinging,
  IconCalendarTime,
  IconChartBar,
  IconEye,
  IconMessage,
  IconShieldCheck,
  IconTerminal2,
  IconUser,
} from "@tabler/icons-react";
import type { LinkProps } from "@tanstack/react-router";
import { m } from "@/paraglide/messages.js";

/** Destinations, grouped by intent: the work, work that starts on its own, and oversight. */
export const SECTIONS = [
  {
    group: "work",
    hint: m.nav_tasks_you_gave_the_agent,
    icon: IconMessage,
    id: "work",
    label: m.nav_work,
    path: "/",
  },
  {
    group: "work",
    hint: m.nav_actions_waiting_for_a_decision,
    icon: IconBellRinging,
    id: "approvals",
    label: m.nav_needs_you,
    path: "/approvals",
  },
  {
    group: "automation",
    hint: m.nav_pages_and_apis_checked_on_a_timetable,
    icon: IconEye,
    id: "watch",
    label: m.nav_watch,
    path: "/watch",
  },
  {
    group: "automation",
    hint: m.nav_tasks_that_start_on_their_own,
    icon: IconCalendarTime,
    id: "schedules",
    label: m.nav_schedules,
    path: "/schedules",
  },
  {
    group: "automation",
    hint: m.nav_one_off_isolated_runs,
    icon: IconTerminal2,
    id: "runs",
    label: m.nav_code_runs,
    path: "/runs",
  },
  {
    group: "oversight",
    hint: m.nav_what_the_agent_may_do,
    icon: IconShieldCheck,
    id: "guardrails",
    label: m.nav_guardrails,
    path: "/guardrails",
  },
  {
    group: "oversight",
    hint: m.nav_tokens_cost_and_time,
    icon: IconChartBar,
    id: "usage",
    label: m.nav_usage,
    path: "/usage",
  },
] as const satisfies readonly {
  group: string;
  hint: () => string;
  icon: unknown;
  id: string;
  label: () => string;
  path: LinkProps["to"];
}[];

export type Section = (typeof SECTIONS)[number];

/** Account sits under the list, and a chat belongs to Work. */
export function isAccountPath(pathname: string): boolean {
  return pathname === "/account" || pathname.startsWith("/account/");
}

/** Chats belong to Work, so an open chat keeps Work marked as the current place. */
export function isActiveSection(section: Section, pathname: string): boolean {
  if (section.path === "/") {
    return pathname === "/" || pathname.startsWith("/chats");
  }
  return pathname === section.path || pathname.startsWith(`${section.path}/`);
}

/** The highlighted destination, or nothing when the path is none of them. */
export function placeFor(
  pathname: string,
): { icon: Section["icon"]; label: () => string } | undefined {
  const section = SECTIONS.find((item) => isActiveSection(item, pathname));
  if (section) {
    return section;
  }
  if (isAccountPath(pathname)) {
    return { icon: IconUser, label: m.nav_account };
  }
  return undefined;
}
