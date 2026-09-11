/** Minimal async-resource hooks. No state framework: this application has no shared mutable state. */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Load a value once and expose loading and error states.
 *
 * `deps` controls reloading. The loader is held in a ref so callers can pass an inline arrow
 * function without re-fetching on every render.
 */
export function useResource<T>(loader: () => Promise<T>, deps: readonly unknown[] = []): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loaderRef.current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof ApiError ? cause : new ApiError("internal_error", String(cause), 0));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

/**
 * Poll a value while `active` is true. Used as the fallback refresh when a live stream dies, and to
 * follow an investigation's derived state while it runs.
 */
export function usePolledResource<T>(loader: () => Promise<T>, active: boolean, intervalMs = 2000,
                                     deps: readonly unknown[] = []): Resource<T> {
  const resource = useResource(loader, deps);
  const reload = resource.reload;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(reload, intervalMs);
    return () => clearInterval(timer);
  }, [active, intervalMs, reload]);
  return resource;
}
