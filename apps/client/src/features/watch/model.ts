import {
  type AlertKind,
  isRecord,
  type Source,
  type SourceSpec,
} from "@/api/types.ts";
import type { StatusMeta } from "@/components/status-text.tsx";
import { m } from "@/paraglide/messages.js";

export const ALERT_KINDS: readonly {
  value: AlertKind;
  label: () => string;
}[] = [
  { label: m.watch_changed, value: "changed" },
  { label: m.watch_gone, value: "gone" },
  { label: m.watch_unavailable, value: "unavailable" },
  { label: m.watch_sign_in_lost, value: "auth_lost" },
  { label: m.watch_limit_hit, value: "limit" },
  { label: m.watch_stale, value: "stale" },
  { label: m.watch_structure_changed, value: "schema_changed" },
];

export const alertKindLabel = (kind: string): string =>
  ALERT_KINDS.find((item) => item.value === kind)?.label() ?? kind;

export function sourceStatus(source: Source): StatusMeta {
  if (source.status === "draft") {
    return { label: m.watch_draft_not_checking(), tone: "attention" };
  }
  if (source.status === "paused") {
    return source.pause_reason === "auth_lost"
      ? { label: m.watch_paused_sign_in_lost(), tone: "danger" }
      : { label: m.watch_paused(), tone: "neutral" };
  }
  return source.draft
    ? { label: m.watch_watching_revision_waiting(), tone: "attention" }
    : { label: m.watch_watching(), tone: "live" };
}

/** The API returns a source's rules as stored JSON; they always have the specification's shape. */
export function specOf(value: unknown): SourceSpec | null {
  return isRecord(value) &&
    typeof value.url === "string" &&
    isRecord(value.schedule)
    ? (value as unknown as SourceSpec)
    : null;
}

/** Resource keys name the source URL itself with an empty string. */
export const resourceLabel = (key: string): string =>
  key === "" ? m.watch_the_address_itself() : key;
