import {
  type InfiniteData,
  infiniteQueryOptions,
  type QueryKey,
} from "@tanstack/react-query";
import { ApiError } from "@/api/errors.ts";
import type { PaginationInfo } from "@/api/types.ts";

/** Retrying helps only when the backend says it is temporarily unavailable. */
export function retry(failures: number, error: unknown): boolean {
  return error instanceof ApiError && error.status === 503 && failures < 2;
}

/** One page of a list: the items and the `PaginationInfo` that describes them. */
export interface Page<T> {
  items: T[];
  info: PaginationInfo;
}

const emptyInfo = (perPage: number): PaginationInfo => ({
  cursor: null,
  has_next: false,
  page: 1,
  per_page: perPage,
  total_items: 0,
  total_pages: 0,
});

/** Cursor pagination: the next page anchors on `info.cursor` while `has_next` is set. */
export function pagesOptions<T>(
  queryKey: QueryKey,
  fetchPage: (cursor: string | null, signal: AbortSignal) => Promise<Page<T>>,
) {
  return infiniteQueryOptions({
    getNextPageParam: (last: Page<T>) =>
      last.info.has_next && last.info.cursor ? last.info.cursor : undefined,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => fetchPage(pageParam, signal),
    queryKey,
  });
}

/** Page-number pagination, for lists that do not take a cursor. */
export function numberedPages<T>(
  queryKey: QueryKey,
  fetchPage: (page: number, signal: AbortSignal) => Promise<Page<T>>,
) {
  return infiniteQueryOptions({
    getNextPageParam: (last: Page<T>) =>
      last.info.has_next ? last.info.page + 1 : undefined,
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) => fetchPage(pageParam, signal),
    queryKey,
  });
}

/** Every item of a cursor-paginated list, for views that must show the whole history. */
export async function allPages<T>(
  fetchPage: (cursor: string | null) => Promise<Page<T>>,
): Promise<Page<T>> {
  const items: T[] = [];
  let cursor: string | null = null;
  let info = emptyInfo(0);
  do {
    const page: Page<T> = await fetchPage(cursor);
    items.push(...page.items);
    info = page.info;
    cursor = page.info.has_next ? page.info.cursor : null;
  } while (cursor);
  return { info: { ...info, cursor: null, has_next: false }, items };
}

/** Every item of a page-numbered list. */
export async function allNumbered<T>(
  fetchPage: (page: number) => Promise<Page<T>>,
): Promise<T[]> {
  const items: T[] = [];
  let page = 1;
  let info = emptyInfo(0);
  do {
    const result = await fetchPage(page);
    items.push(...result.items);
    info = result.info;
    page += 1;
  } while (info.has_next);
  return items;
}

export function flatten<T>(data: InfiniteData<Page<T>> | undefined): T[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}
