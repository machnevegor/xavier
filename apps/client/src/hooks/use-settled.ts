import { useEffect, useState } from "react";

/**
 * The value once it has stopped changing for `delay` ms, and null before that. Expensive
 * renderers draw a streamed block once it is complete instead of on every chunk.
 */
export function useSettled<T>(value: T, delay = 250): T | null {
  const [settled, setSettled] = useState<T | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
