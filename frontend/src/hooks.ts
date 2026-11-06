import { useCallback, useEffect, useRef, useState } from "react";

/** Runs an async action, tracking loading state, errors and elapsed seconds (LLM calls on CPU are slow). */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearInterval(timer.current), []);

  const run = useCallback(async <T,>(label: string, action: () => Promise<T>): Promise<T | undefined> => {
    setBusy(label);
    setError(null);
    setElapsed(0);
    const started = Date.now();
    timer.current = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    try {
      return await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      window.clearInterval(timer.current);
      setBusy(null);
    }
  }, []);

  return { busy, error, elapsed, run, clearError: () => setError(null) };
}
