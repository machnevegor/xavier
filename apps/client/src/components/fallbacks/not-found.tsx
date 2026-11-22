import { IconMapQuestion } from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { Button } from "@/components/ui/button.tsx";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty.tsx";
import { m } from "@/paraglide/messages.js";

export function NotFound() {
  return (
    <Empty className="min-h-[50dvh]" data-fallback>
      <title>{m.meta_not_found_xavier()}</title>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <IconMapQuestion />
        </EmptyMedia>
        <EmptyTitle>{m.meta_there_is_nothing_here()}</EmptyTitle>
        <EmptyDescription>
          {m.meta_this_page_doesnt_exist_or_its_content_was()}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="flex-row justify-center gap-2">
        <Button
          onClick={() => window.history.back()}
          size="sm"
          variant="outline"
        >
          {m.meta_back()}
        </Button>
        <Button nativeButton={false} render={<Link to="/" />} size="sm">
          {m.meta_go_to_work()}
        </Button>
      </EmptyContent>
    </Empty>
  );
}
