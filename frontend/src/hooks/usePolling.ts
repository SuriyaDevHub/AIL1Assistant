import { useEffect, useRef } from "react";

/** Re-runs `callback` every `intervalMs` while `enabled` is true. Holds the
 * callback in a ref so the interval doesn't need to be torn down and
 * recreated on every render just because the callback identity changed. */
export function usePolling(callback: () => void, intervalMs: number, enabled = true): void {
  const savedCallback = useRef(callback);
  savedCallback.current = callback;

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => savedCallback.current(), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
