/**
 * Shared system metadata (health, meta, providers), loaded once and shared through context so the
 * shell and every page agree on what environment they are showing.
 */

import { createContext, createElement, useContext, type ReactNode } from "react";
import { getHealth, getMeta, getProviders, type ApiError } from "@/api/client";
import { useResource } from "@/hooks/useApi";
import type { Health, Meta, ProvidersResponse } from "@/types/api";

interface SystemValue {
  meta: Meta | null;
  health: Health | null;
  providers: ProvidersResponse | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
}

const SystemContext = createContext<SystemValue | null>(null);

export function SystemProvider({ children }: { children: ReactNode }) {
  const health = useResource(() => getHealth(), []);
  const meta = useResource(() => getMeta(), []);
  const providers = useResource(() => getProviders(), []);

  const value: SystemValue = {
    meta: meta.data,
    health: health.data,
    providers: providers.data,
    loading: health.loading || meta.loading || providers.loading,
    error: health.error ?? meta.error ?? providers.error,
    reload: () => {
      health.reload();
      meta.reload();
      providers.reload();
    },
  };

  return createElement(SystemContext.Provider, { value }, children);
}

export function useSystem(): SystemValue {
  const value = useContext(SystemContext);
  if (!value) throw new Error("useSystem must be used inside SystemProvider");
  return value;
}
