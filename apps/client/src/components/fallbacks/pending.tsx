import { Logogrid } from "@/components/brand/logogrid.tsx";
import { m } from "@/paraglide/messages.js";

export function Pending() {
  return (
    <div
      aria-label={m.meta_loading()}
      className="flex min-h-[50dvh] flex-1 items-center justify-center"
      data-fallback
      role="status"
    >
      <Logogrid className="text-primary w-20" />
    </div>
  );
}
