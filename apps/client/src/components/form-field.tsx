import type { ReactNode } from "react";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field.tsx";

/** A labelled control with its help text and, once the form is checked, its error. */
export function FormField({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string;
  label: ReactNode;
  help?: ReactNode;
  error?: string | undefined;
  children: ReactNode;
}) {
  return (
    <Field data-invalid={error ? true : undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children}
      {help && <FieldDescription>{help}</FieldDescription>}
      {error && <FieldError>{error}</FieldError>}
    </Field>
  );
}
