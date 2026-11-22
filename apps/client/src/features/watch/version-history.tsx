import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { useState } from "react";
import { flatten } from "@/api/query.ts";
import { isRecord, type Version } from "@/api/types.ts";
import { JsonBlock } from "@/components/code-block.tsx";
import { Disclosure } from "@/components/disclosure.tsx";
import {
  ErrorState,
  LoadingRows,
  LoadMore,
  QueryBoundary,
} from "@/components/states.tsx";
import { watchQueries } from "@/features/watch/api.ts";
import { resourceLabel } from "@/features/watch/model.ts";
import { bytes, dateTime } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const shown = (value: unknown): string =>
  value === undefined
    ? ""
    : typeof value === "string"
      ? value
      : JSON.stringify(value);

const VALUE_OP: Record<string, () => string> = {
  added: m.watch_source_added,
  changed: m.watch_source_changed,
  removed: m.watch_source_removed,
};
const SCHEMA_OP: Record<string, () => string> = {
  added: m.watch_source_field_added,
  removed: m.watch_source_field_removed,
  type_changed: m.watch_source_type_changed,
};

/** One documented change record: what happened at a path, with the value before and after. */
function ChangeRow({
  change,
  labels,
  before,
  after,
}: {
  change: unknown;
  labels: Record<string, () => string>;
  before: string;
  after: string;
}) {
  if (!isRecord(change)) {
    return (
      <li className="py-2">
        <JsonBlock value={change} />
      </li>
    );
  }
  const kind = shown(change.op ?? change.change);
  return (
    <li className="grid gap-1 py-2 sm:grid-cols-[8rem_1fr]">
      <span className="text-muted-foreground">{labels[kind]?.() ?? kind}</span>
      <div className="min-w-0">
        <p className="font-mono text-xs break-all">{shown(change.path)}</p>
        {change[before] !== undefined && (
          <p className="text-destructive mt-1 break-words line-through decoration-1">
            {shown(change[before])}
          </p>
        )}
        {change[after] !== undefined && (
          <p className="text-ok mt-1 break-words">{shown(change[after])}</p>
        )}
      </div>
    </li>
  );
}

function Diff({
  sourceId,
  from,
  to,
}: {
  sourceId: string;
  from: string;
  to: string;
}) {
  const diff = useQuery(watchQueries.diff(sourceId, from, to));
  return (
    <QueryBoundary query={diff} rows={2}>
      {(data) => (
        <div className="space-y-3">
          <p className="text-muted-foreground">
            {m.watch_source_value_change({ count: data.change_count })}
            {data.schema_changes.length > 0 &&
              m.watch_source_structure_change({
                count: data.schema_changes.length,
              })}{" "}
            {m.watch_source_compared_with_the_version_before()}
            {data.changes.length < data.change_count &&
              ` ${m.watch_source_the_first_are_listed({ n: data.changes.length })}`}
          </p>
          {data.changes.length > 0 && (
            <ul className="divide-y border-y">
              {data.changes.map((change, index) => (
                <ChangeRow
                  after="new"
                  before="old"
                  change={change}
                  key={index}
                  labels={VALUE_OP}
                />
              ))}
            </ul>
          )}
          {data.schema_changes.length > 0 && (
            <ul
              aria-label={m.watch_source_structure_changes()}
              className="divide-y border-y"
            >
              {data.schema_changes.map((change, index) => (
                <ChangeRow
                  after="to"
                  before="from"
                  change={change}
                  key={index}
                  labels={SCHEMA_OP}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </QueryBoundary>
  );
}

function VersionValue({
  sourceId,
  versionId,
}: {
  sourceId: string;
  versionId: string;
}) {
  const value = useQuery(watchQueries.value(sourceId, versionId));
  return (
    <QueryBoundary query={value} rows={2}>
      {(data) => <JsonBlock value={data.value} />}
    </QueryBoundary>
  );
}

function Selected({
  sourceId,
  version,
  previous,
}: {
  sourceId: string;
  version: Version;
  previous: Version | undefined;
}) {
  return (
    <div className="min-w-0 space-y-3">
      <p className="text-muted-foreground font-mono text-xs break-all">
        {version.url}
      </p>
      {version.gone ? (
        <p>{m.watch_source_the_resource_was_unavailable_during_this_check()}</p>
      ) : previous ? (
        <Diff from={previous.id} sourceId={sourceId} to={version.id} />
      ) : (
        <p className="text-muted-foreground">
          {m.watch_source_this_is_the_first_recorded_version_of_this()}
        </p>
      )}
      {!version.gone && (
        <Disclosure label={m.watch_source_show_the_observed_value()}>
          <div className="mt-2">
            <VersionValue sourceId={sourceId} versionId={version.id} />
          </div>
        </Disclosure>
      )}
    </div>
  );
}

/** Versions newest first. Selecting one shows what changed against the one before it. */
export function VersionHistory({ sourceId }: { sourceId: string }) {
  const versions = useInfiniteQuery(watchQueries.versions(sourceId));
  const [selected, setSelected] = useState<string | null>(null);
  const items = flatten(versions.data);
  const many = new Set(items.map((item) => item.resource_key)).size > 1;

  if (versions.isPending) {
    return <LoadingRows />;
  }
  if (versions.isError) {
    return (
      <ErrorState
        error={versions.error}
        onRetry={() => void versions.refetch()}
      />
    );
  }
  if (items.length === 0) {
    return (
      <p className="text-muted-foreground">
        {m.watch_source_no_versions_yet_the_first_check_records_one()}
      </p>
    );
  }
  const index = Math.max(
    0,
    items.findIndex((version) => version.id === selected),
  );
  const current = items[index];
  const previous = current
    ? items
        .slice(index + 1)
        .find((version) => version.resource_key === current.resource_key)
    : undefined;

  return (
    <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
      <div>
        <ul
          aria-label={m.watch_source_versions()}
          className="divide-y border-y md:max-h-96 md:overflow-y-auto"
        >
          {items.map((version, i) => (
            <li key={version.id}>
              <button
                aria-current={i === index ? "true" : undefined}
                className={cn(
                  "w-full px-2 py-2.5 text-left",
                  i === index
                    ? "bg-secondary font-medium"
                    : "hover:bg-accent/50",
                )}
                onClick={() => setSelected(version.id)}
                type="button"
              >
                {dateTime(version.created_at)}
                <span className="text-muted-foreground block text-xs font-normal">
                  {many && `${resourceLabel(version.resource_key)} / `}
                  {version.gone
                    ? m.watch_source_gone()
                    : m.watch_source_change({ count: version.change_count })}
                  {" / "}
                  {bytes(version.size_bytes)}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <LoadMore {...versions} />
      </div>
      {current && (
        <Selected previous={previous} sourceId={sourceId} version={current} />
      )}
    </div>
  );
}
