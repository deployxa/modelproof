/** Shared UI primitives + tiny hash router (no external dependency: the
 *  dashboard must build and run fully offline). */
import { useEffect, useState, type ReactNode } from "react";

// ------------------------------------------------------------------ router
export function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash || "#/home");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/home");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export function navigate(to: string) {
  window.location.hash = to;
}

// ------------------------------------------------------------------ helpers
export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n;
  let u = -1;
  do { v /= 1024; u++; } while (v >= 1024 && u < units.length - 1);
  return `${v.toFixed(1)} ${units[u]}`;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  } catch {
    return iso;
  }
}

export function Badge({ value, children }: { value: string; children?: ReactNode }) {
  return <span className={`badge b-${value}`}>{children ?? value.replace(/_/g, " ")}</span>;
}

export function Loading({ label = "loading" }: { label?: string }) {
  return <div className="note mt"><span className="spin" /> {label}…</div>;
}

export function ErrorBox({ error, hint }: { error: string | null; hint?: ReactNode }) {
  if (!error) return null;
  return (
    <div className="error-panel">
      {error}
      {hint ? <div className="note mt">{hint}</div> : null}
    </div>
  );
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <div className="kv">
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <div className="k">{k}</div>
          <div className="v">{v}</div>
        </div>
      ))}
    </div>
  );
}

export function Card({ title, children, actions }: { title?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="card">
      {(title || actions) && (
        <div style={{ display: "flex", alignItems: "baseline", gap: "0.6rem" }}>
          {title && <h3 style={{ marginBottom: 0 }}>{title}</h3>}
          {actions && <div style={{ marginLeft: "auto" }}>{actions}</div>}
        </div>
      )}
      {title && <div style={{ height: "0.5rem" }} />}
      {children}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge value={status} />;
}

/** Statuses the UI treats as "run integrity/verdict" classes — consistent
 *  coloring via styles.css b-* classes; unknown statuses fall back to neutral. */
export function GateBadge({ outcome }: { outcome: string }) {
  const known = ["PASS", "FAIL", "WARN", "BLOCKED", "NOT_RUN"];
  return <Badge value={known.includes(outcome) ? outcome : "NOT_RUN"}>{outcome}</Badge>;
}
