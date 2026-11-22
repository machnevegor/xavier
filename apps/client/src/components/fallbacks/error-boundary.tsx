import { IconAlertTriangle } from "@tabler/icons-react";
import {
  type ErrorComponentProps,
  Link,
  rootRouteId,
  useMatch,
  useRouter,
} from "@tanstack/react-router";
import { explain } from "@/api/errors.ts";
import { CopyButton } from "@/components/copy-button.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty.tsx";
import { useTelemetrySessionId } from "@/lib/telemetry.ts";
import { m } from "@/paraglide/messages.js";

export function ErrorBoundary({ error }: ErrorComponentProps) {
  const router = useRouter();
  const isRoot = useMatch({
    select: (match) => match.routeId === rootRouteId,
    strict: false,
  });
  const sessionId = useTelemetrySessionId();
  const { title, hint } = explain(error);

  return (
    <Empty className="min-h-[50dvh]" data-fallback>
      <EmptyHeader>
        <EmptyMedia className="text-destructive" variant="icon">
          <IconAlertTriangle />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {hint && <EmptyDescription>{hint}</EmptyDescription>}
      </EmptyHeader>
      <EmptyContent>
        <div className="flex gap-2">
          <Button
            onClick={() => router.invalidate()}
            size="sm"
            variant="outline"
          >
            {m.meta_try_again()}
          </Button>
          {isRoot ? (
            <Button nativeButton={false} render={<Link to="/" />} size="sm">
              {m.meta_go_to_work()}
            </Button>
          ) : (
            <Button onClick={() => window.history.back()} size="sm">
              {m.meta_back()}
            </Button>
          )}
        </div>
        {sessionId && (
          <CopyButton
            className="text-muted-foreground max-w-full font-mono"
            label={m.meta_copy_diagnostic_code()}
            value={sessionId}
          >
            <span className="truncate">{sessionId}</span>
          </CopyButton>
        )}
      </EmptyContent>
    </Empty>
  );
}
