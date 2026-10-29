import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ModelProof — local evaluation lab",
  description:
    "Local-first, evidence-first evaluation lab for local coding models. Sandboxed execution, reproducible runs, verifiable evidence bundles.",
};

export default function Home() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: "100vh",
        background: "#0d1117",
      }}
    >
      <iframe
        src="/mp/index.html"
        title="ModelProof dashboard"
        style={{ flex: 1, width: "100%", border: "none", minHeight: "calc(100vh - 34px)" }}
      />
      <div
        style={{
          background: "#161b22",
          borderTop: "1px solid #2b3442",
          color: "#8b97a6",
          fontSize: 12,
          padding: "8px 14px",
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      >
        ModelProof engine: loopback-only (127.0.0.1:8899) · inference local-only · generated code
        runs sandboxed, never on the host ·{" "}
        <a href="/mp/index.html" style={{ color: "#4cc38a" }}>
          open dashboard full-screen
        </a>
      </div>
    </div>
  );
}
