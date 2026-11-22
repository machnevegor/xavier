import type { QueryClient } from "@tanstack/react-query";
import type { Me } from "@/api/types.ts";
import { updateMe } from "@/features/auth/api.ts";
import { getLocale, setLocale } from "@/paraglide/runtime.js";

/**
 * Make the profile and the device cookie agree before the signed-in UI renders.
 *
 * An empty profile saves the language already on screen. A saved language that
 * differs from the cookie reloads the same URL and never resolves, so nothing
 * paints in the old language.
 */
export async function syncProfileLocale(
  me: Me,
  queryClient: QueryClient,
): Promise<void> {
  const saved = me.user.locale;
  if (saved == null) {
    await updateMe(getLocale(), queryClient);
    return;
  }
  if (saved !== getLocale()) {
    setLocale(saved);
    return new Promise(() => {});
  }
}
