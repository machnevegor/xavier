import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { api, read, readPage, unwrap } from "@/api/client.ts";
import { allPages } from "@/api/query.ts";
import type { ApiKey, Me, Session } from "@/api/types.ts";

/** Who is signed in. A 401 means the cookie is missing or no longer valid. */
export const sessionQuery = () =>
  queryOptions({
    queryFn: ({ signal }) => read(api.GET("/api/v1/auth/me", { signal })),
    queryKey: ["me"],
    staleTime: 60_000,
  });

/** Sign in. The password is not kept; the session lives in the HttpOnly cookie. */
export async function signIn(
  username: string,
  password: string,
): Promise<void> {
  await unwrap(
    api.POST("/api/v1/auth/session", {
      body: { data: { password, username } },
    }),
  );
}

export async function signOut(): Promise<void> {
  await unwrap(api.DELETE("/api/v1/auth/session"));
}

/** Save the interface language and refresh the signed-in user cache. */
export async function updateMe(
  locale: NonNullable<Me["user"]["locale"]>,
  queryClient: QueryClient,
): Promise<Me> {
  const me = await read(
    api.PATCH("/api/v1/auth/me", { body: { data: { locale } } }),
  );
  queryClient.setQueryData(sessionQuery().queryKey, me);
  return me;
}

/** Change the password; the server keeps this browser session and revokes the others. */
export const updatePassword = (oldPassword: string, newPassword: string) =>
  read(
    api.PATCH("/api/v1/auth/me/password", {
      body: {
        data: {
          current_password: oldPassword,
          new_password: newPassword,
        },
      },
    }),
  );

const PER_PAGE = 50;

export const accountQueries = {
  keys: () =>
    queryOptions({
      queryFn: ({ signal }) =>
        allPages((cursor) =>
          readPage(
            api.GET("/api/v1/api-keys", {
              params: { query: { cursor, per_page: PER_PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: ["api-keys"],
    }),
  sessions: () =>
    queryOptions({
      queryFn: ({ signal }) =>
        allPages((cursor) =>
          readPage(
            api.GET("/api/v1/sessions", {
              params: { query: { cursor, per_page: PER_PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: ["sessions"],
    }),
};

export const createApiKey = (name: string) =>
  unwrap(api.POST("/api/v1/api-keys", { body: { data: { name } } }));

export const revokeApiKey = (id: string) =>
  read(
    api.DELETE("/api/v1/api-keys/{api_key_id}", {
      params: { path: { api_key_id: id } },
    }),
  );

export const revokeSession = (id: string): Promise<Session> =>
  read(
    api.DELETE("/api/v1/sessions/{session_id}", {
      params: { path: { session_id: id } },
    }),
  );

export type { ApiKey };

export const healthQuery = () =>
  queryOptions({
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/readyz", { signal })),
    queryKey: ["readyz"],
  });
