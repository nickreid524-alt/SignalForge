/** Small shared building blocks. Deliberately plain: no UI kit, no styling framework. */

import type { ReactNode } from "react";

export function Panel({ title, actions, children, flush = false, className = "" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title !== undefined && (
        <header className="panel__header">
          <h2 className="panel__title">{title}</h2>
          {actions}
        </header>
      )}
      <div className={flush ? "panel__body panel__body--flush" : "panel__body"}>{children}</div>
    </section>
  );
}

export function Badge({ tone = "neutral", children, title }: {
  tone?: "neutral" | "ok" | "warn" | "danger" | "accent" | "live" | "synthetic";
  children: ReactNode;
  title?: string;
}) {
  const suffix = tone === "neutral" ? "" : ` badge--${tone}`;
  return <span className={`badge${suffix}`} title={title}>{children}</span>;
}

/** Severity is shown as text plus colour, never colour alone. */
export function Severity({ value }: { value: string }) {
  const key = value.toLowerCase().replace(/[^a-z0-9]/g, "");
  const known = ["sev1", "sev2", "sev3", "sev4"].includes(key);
  return <span className={`sev ${known ? `sev--${key}` : ""}`}>{value.toUpperCase()}</span>;
}

export function EmptyState({ title, children, tone = "neutral", inline = false, action }: {
  title: string;
  children?: ReactNode;
  tone?: "neutral" | "error";
  inline?: boolean;
  action?: ReactNode;
}) {
  return (
    <div className={`state${tone === "error" ? " state--error" : ""}${inline ? " state--inline" : ""}`} role={tone === "error" ? "alert" : undefined}>
      <p className="state__title">{title}</p>
      {children && <div className="state__body">{children}</div>}
      {action}
    </div>
  );
}

export function Loading({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="state state--inline" aria-live="polite" aria-busy="true">
      <span className="visually-hidden">{label}</span>
      <div className="stack stack--tight" style={{ width: "100%", maxWidth: 520 }} aria-hidden="true">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="skeleton" style={{ height: 13, width: `${100 - index * 12}%` }} />
        ))}
      </div>
    </div>
  );
}

export function Banner({ tone = "info", children, title }: {
  tone?: "info" | "warn" | "danger" | "live" | "neutral";
  children: ReactNode;
  title?: string;
}) {
  return (
    <div className={`banner${tone === "neutral" ? "" : ` banner--${tone}`}`} role={tone === "danger" ? "alert" : "note"}>
      <div>
        {title && <strong style={{ display: "block", marginBottom: 2 }}>{title}</strong>}
        {children}
      </div>
    </div>
  );
}

export function Metric({ label, value, hint, tone }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "ok" | "warn" | "danger";
}) {
  const colour = tone === "ok" ? "var(--ok)" : tone === "warn" ? "var(--warn)" : tone === "danger" ? "var(--danger)" : "var(--fg)";
  return (
    <div className="panel" style={{ padding: "var(--space-4)" }}>
      <div className="xs muted" style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
        {label}
      </div>
      <div style={{ fontSize: "var(--text-2xl)", fontWeight: 600, color: colour, lineHeight: 1.2, marginTop: 6 }}>
        {value}
      </div>
      {hint && <div className="xs muted" style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

/**
 * A confidence bar. Values are shown to two decimals because the engine reports two; anything more
 * would be misleading precision.
 */
export function Confidence({ value, tone = "var(--accent)", showValue = true }: {
  value: number;
  tone?: string;
  showValue?: boolean;
}) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span className="row" style={{ gap: 8 }}>
      <span
        role="meter"
        aria-valuenow={Number(value.toFixed(2))}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-label={`Confidence ${value.toFixed(2)}`}
        style={{
          display: "inline-block", width: 56, height: 5, borderRadius: 3,
          background: "var(--border)", overflow: "hidden", flex: "none",
        }}
      >
        <span style={{ display: "block", width: `${pct}%`, height: "100%", background: tone }} />
      </span>
      {showValue && <span className="mono small" style={{ color: "var(--fg-muted)" }}>{value.toFixed(2)}</span>}
    </span>
  );
}

/**
 * JSON shown as escaped text inside a <pre>. Never dangerouslySetInnerHTML: this renders evidence
 * payloads, which are untrusted retrieved content.
 */
export function JsonBlock({ value, maxHeight = 320 }: { value: unknown; maxHeight?: number }) {
  return (
    <pre
      className="mono"
      style={{
        margin: 0, padding: "var(--space-3)", background: "var(--surface-sunken)",
        border: "1px solid var(--border-hairline)", borderRadius: "var(--radius-sm)",
        fontSize: "var(--text-sm)", lineHeight: 1.55, overflow: "auto", maxHeight,
        whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** Collapsible section built on <details> so keyboard and screen-reader behaviour come for free. */
export function Collapsible({ summary, children, open = false }: {
  summary: ReactNode;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details open={open} style={{ borderTop: "1px solid var(--border-hairline)" }}>
      <summary
        style={{
          cursor: "pointer", padding: "var(--space-2) 0", fontSize: "var(--text-sm)",
          color: "var(--fg-muted)", userSelect: "none",
        }}
      >
        {summary}
      </summary>
      <div style={{ paddingBottom: "var(--space-3)" }}>{children}</div>
    </details>
  );
}

export function RelativeTime({ iso, prefix = "" }: { iso: string | null; prefix?: string }) {
  if (!iso) return <span className="dim">—</span>;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return <span className="dim">—</span>;
  return (
    <time dateTime={iso} title={date.toISOString()}>
      {prefix}
      {date.toISOString().slice(0, 16).replace("T", " ")}Z
    </time>
  );
}

export function Duration({ seconds }: { seconds: number | null | undefined }) {
  if (seconds === null || seconds === undefined) return <span className="dim">—</span>;
  if (seconds < 1) return <span className="mono">{Math.round(seconds * 1000)} ms</span>;
  if (seconds < 60) return <span className="mono">{seconds.toFixed(1)} s</span>;
  return <span className="mono">{Math.floor(seconds / 60)}m {Math.round(seconds % 60)}s</span>;
}

export function Latency({ ms }: { ms: number | null | undefined }) {
  if (ms === null || ms === undefined) return null;
  return <span className="mono dim xs">{ms < 1 ? "<1" : Math.round(ms)} ms</span>;
}
