import { queryOptions } from "@tanstack/react-query";
import { api, read, readPage } from "@/api/client.ts";
import { allPages, pagesOptions } from "@/api/query.ts";
import type {
  AlertStatus,
  CredentialCreate,
  CredentialUpdate,
  SourceInput,
  SubscriptionInput,
} from "@/api/types.ts";

export type SourceCommand = "publish" | "pause" | "resume" | "check";
export type AlertCommand = "acknowledge" | "resolve";

const PAGE = 200;

export const watchKeys = {
  alerts: ["alerts"] as const,
  credentials: ["credentials"] as const,
  source: (id: string) => ["source", id] as const,
  sources: ["sources"] as const,
  subscriptions: ["subscriptions"] as const,
};

export const watchQueries = {
  alerts: (status: AlertStatus, sourceId?: string) => ({
    ...pagesOptions(
      [...watchKeys.alerts, status, sourceId ?? null],
      (cursor, signal) =>
        readPage(
          api.GET("/api/v1/alerts", {
            params: {
              query: {
                cursor,
                "filter[source_id]": sourceId ? [sourceId] : undefined,
                "filter[status]": [status],
                per_page: 50,
              },
            },
            signal,
          }),
        ),
    ),
    refetchInterval: 10_000,
  }),
  credentialEvents: (id: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        readPage(
          api.GET("/api/v1/credentials/{credential_id}/events", {
            params: {
              path: { credential_id: id },
              query: { per_page: 20 },
            },
            signal,
          }),
        ),
      queryKey: [...watchKeys.credentials, id, "events"],
    }),
  credentials: (scope?: { chatId?: string; global?: boolean }) =>
    queryOptions({
      queryFn: async ({ signal }) =>
        (
          await allPages((cursor) =>
            readPage(
              api.GET("/api/v1/credentials", {
                params: {
                  query: {
                    cursor,
                    "filter[chat_id]": scope?.chatId
                      ? [scope.chatId]
                      : undefined,
                    "filter[is_global]": scope?.global ? true : undefined,
                    per_page: PAGE,
                  },
                },
                signal,
              }),
            ),
          )
        ).items,
      queryKey: [
        ...watchKeys.credentials,
        scope?.chatId ?? null,
        scope?.global ?? false,
      ],
    }),
  diff: (sourceId: string, from: string, to: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/sources/{source_id}/diff", {
            params: {
              path: { source_id: sourceId },
              query: { from, to },
            },
            signal,
          }),
        ),
      queryKey: [...watchKeys.source(sourceId), "diff", from, to],
      staleTime: Number.POSITIVE_INFINITY,
    }),
  source: (id: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/sources/{source_id}", {
            params: { path: { source_id: id } },
            signal,
          }),
        ),
      queryKey: watchKeys.source(id),
      refetchInterval: 5000,
    }),
  sources: () => ({
    ...pagesOptions(watchKeys.sources, (cursor, signal) =>
      readPage(
        api.GET("/api/v1/sources", {
          params: { query: { cursor, per_page: 50 } },
          signal,
        }),
      ),
    ),
    refetchInterval: 10_000,
  }),
  subscriptions: () =>
    queryOptions({
      queryFn: async ({ signal }) =>
        (
          await allPages((cursor) =>
            readPage(
              api.GET("/api/v1/subscriptions", {
                params: { query: { cursor, per_page: PAGE } },
                signal,
              }),
            ),
          )
        ).items,
      queryKey: watchKeys.subscriptions,
    }),
  value: (sourceId: string, versionId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/sources/{source_id}/versions/{version_id}", {
            params: {
              path: { source_id: sourceId, version_id: versionId },
            },
            signal,
          }),
        ),
      queryKey: [...watchKeys.source(sourceId), "value", versionId],
      staleTime: Number.POSITIVE_INFINITY,
    }),
  versions: (sourceId: string) => ({
    ...pagesOptions(
      [...watchKeys.source(sourceId), "versions"],
      (cursor, signal) =>
        readPage(
          api.GET("/api/v1/sources/{source_id}/versions", {
            params: {
              path: { source_id: sourceId },
              query: { cursor, per_page: 50 },
            },
            signal,
          }),
        ),
    ),
    refetchInterval: 10_000,
  }),
};

export const createSource = (body: SourceInput) =>
  read(api.POST("/api/v1/sources", { body: { data: body } }));

/** A polled source keeps its published rules until the replacement is published. */
export const replaceSource = (id: string, body: SourceInput) =>
  read(
    api.PUT("/api/v1/sources/{source_id}", {
      body: { data: body },
      params: { path: { source_id: id } },
    }),
  );

export const deleteSource = (id: string) =>
  read(
    api.DELETE("/api/v1/sources/{source_id}", {
      params: { path: { source_id: id } },
    }),
  );

export function runSourceCommand(id: string, command: SourceCommand) {
  const params = { params: { path: { source_id: id } } };
  switch (command) {
    case "publish":
      return read(api.POST("/api/v1/sources/{source_id}/publish", params));
    case "pause":
      return read(api.POST("/api/v1/sources/{source_id}/pause", params));
    case "resume":
      return read(api.POST("/api/v1/sources/{source_id}/resume", params));
    case "check":
      return read(api.POST("/api/v1/sources/{source_id}/check", params));
    default:
      return command satisfies never;
  }
}

export function runAlertCommand(id: string, command: AlertCommand) {
  const params = { params: { path: { alert_id: id } } };
  return command === "acknowledge"
    ? read(api.POST("/api/v1/alerts/{alert_id}/acknowledge", params))
    : read(api.POST("/api/v1/alerts/{alert_id}/resolve", params));
}

export const createCredential = (body: CredentialCreate) =>
  read(api.POST("/api/v1/credentials", { body: { data: body } }));

export const updateCredential = (id: string, body: CredentialUpdate) =>
  read(
    api.PATCH("/api/v1/credentials/{credential_id}", {
      body: { data: body },
      params: { path: { credential_id: id } },
    }),
  );

export const revokeCredential = (id: string) =>
  read(
    api.POST("/api/v1/credentials/{credential_id}/revoke", {
      params: { path: { credential_id: id } },
    }),
  );

export const deleteCredential = (id: string) =>
  read(
    api.DELETE("/api/v1/credentials/{credential_id}", {
      params: { path: { credential_id: id } },
    }),
  );

export const createSubscription = (body: SubscriptionInput) =>
  read(api.POST("/api/v1/subscriptions", { body: { data: body } }));

export const deleteSubscription = (id: string) =>
  read(
    api.DELETE("/api/v1/subscriptions/{subscription_id}", {
      params: { path: { subscription_id: id } },
    }),
  );
