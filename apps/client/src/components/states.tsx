import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { explain, isUnauthorized } from "@/api/errors.ts";
import { Button } from "@/components/ui/button.tsx";
import { Skeleton } from "@/components/ui/skeleton.tsx";
import { m } from "@/paraglide/messages.js";

/** In place and persistent: what failed, what to do, and the way out. */
export function ErrorState({
  error,
  onRetry,
  children,
}: {
  error: unknown;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  const { title, hint } = explain(error);
  const signedOut = isUnauthorized(error);
  return (
    <div className="border-destructive border-l-2 py-0.5 pl-4" role="alert">
      <p className="font-medium">{title}</p>
      {hint && <p className="text-muted-foreground mt-1">{hint}</p>}
      <div className="mt-3 flex flex-wrap gap-2 empty:hidden">
        {signedOut && (
          <Button
            nativeButton={false}
            render={<Link to="/sign-in" />}
            size="sm"
          >
            {m.common_sign_in()}
          </Button>
        )}
        {onRetry && !signedOut && (
          <Button onClick={onRetry} size="sm" variant="outline">
            {m.common_try_again()}
          </Button>
        )}
        {children}
      </div>
    </div>
  );
}

/** Sits where the list would be, between the same two rules, so the page keeps its shape. */
export function EmptyState({
  title,
  children,
}: {
  title: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="border-y py-8">
      <p className="font-medium">{title}</p>
      {children && (
        <div className="text-muted-foreground mt-1 max-w-md">{children}</div>
      )}
    </div>
  );
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="space-y-3">
      <span className="sr-only">{m.common_loading()}</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton className="h-12 w-full" key={index} />
      ))}
    </div>
  );
}

/** Loading keeps the shape of the content; errors stay on screen with a way out. */
export function QueryBoundary<T>({
  query,
  rows,
  children,
}: {
  query: UseQueryResult<T>;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) {
    return <LoadingRows rows={rows} />;
  }
  if (query.isError) {
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  }
  return children(query.data);
}

export function LoadMore({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}) {
  if (!hasNextPage) {
    return null;
  }
  return (
    <div className="pt-3">
      <Button
        disabled={isFetchingNextPage}
        onClick={() => fetchNextPage()}
        size="sm"
        variant="outline"
      >
        {isFetchingNextPage ? m.common_loading_2() : m.common_load_more()}
      </Button>
    </div>
  );
}
