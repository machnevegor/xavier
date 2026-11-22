import handler from "@tanstack/react-start/server-entry";
import { getLocale } from "@/paraglide/runtime.js";
import { paraglideMiddleware } from "@/paraglide/server.js";

const COOKIE = "xavier_locale";
const ONE_YEAR = 60 * 60 * 24 * 365;

/** True when this request already carried the locale cookie. */
function hasLocaleCookie(request: Request): boolean {
  const header = request.headers.get("cookie");
  if (!header) {
    return false;
  }
  return header.split(";").some((part) => part.trim().startsWith(`${COOKIE}=`));
}

export default {
  fetch(request: Request): Promise<Response> {
    const firstVisit = !hasLocaleCookie(request);
    return paraglideMiddleware(request, async () => {
      const response = await handler.fetch(request);
      if (!firstVisit) {
        return response;
      }
      const headers = new Headers(response.headers);
      headers.append(
        "set-cookie",
        `${COOKIE}=${getLocale()}; Path=/; Max-Age=${ONE_YEAR}; SameSite=Lax`,
      );
      return new Response(response.body, {
        headers,
        status: response.status,
        statusText: response.statusText,
      });
    });
  },
};
