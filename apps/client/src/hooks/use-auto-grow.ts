import { useLayoutEffect, useRef } from "react";

/** A text box that grows with its text up to its own max-height, then scrolls. */
export function useAutoGrow() {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const field = ref.current;
    if (!field) {
      return;
    }
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  });
  return ref;
}
