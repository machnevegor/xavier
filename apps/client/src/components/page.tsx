import { Link, type LinkProps } from "@tanstack/react-router";
import { cn } from "cn";
import { Fragment, type ReactNode } from "react";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb.tsx";

/** Every page is centred beside the navigation; sections are separated by space alone. */
export function Page({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={cn("page", className)}>{children}</div>;
}

export function PageHeader({
  title,
  lead,
  children,
}: {
  title: ReactNode;
  lead?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="type-title break-words">{title}</h1>
        {lead && <p className="text-muted-foreground mt-1 max-w-2xl">{lead}</p>}
      </div>
      {children && (
        <div className="flex flex-wrap items-center gap-2">{children}</div>
      )}
    </header>
  );
}

export function Section({
  title,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="type-section">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export interface Crumb {
  label: string;
  to?: LinkProps["to"];
  params?: LinkProps["params"];
}

/**
 * Where you are inside a section; the last item is the current page. It sits close above the
 * page title whatever the page's own spacing.
 */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <Breadcrumb className="mb-3">
      <BreadcrumbList>
        {items.map((item, index) => (
          <Fragment key={item.label}>
            {index > 0 && <BreadcrumbSeparator />}
            <BreadcrumbItem>
              {item.to ? (
                <BreadcrumbLink
                  render={<Link params={item.params} to={item.to} />}
                >
                  {item.label}
                </BreadcrumbLink>
              ) : (
                <BreadcrumbPage className="max-w-[40ch] truncate">
                  {item.label}
                </BreadcrumbPage>
              )}
            </BreadcrumbItem>
          </Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  );
}
