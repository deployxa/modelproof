/** Screen H — Settings, maintenance, diagnostics, storage, release gates. */
import { useEffect, useState } from "react";
import { Api, type Diagnostics, type DoctorCheck, type ReleaseReport, type StorageInfo } from "../api";
import { Badge, Card, ErrorBox, GateBadge, KV, Loading, fmtBytes, fmtTime } from "../ui";

export default function Settings() {
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [doctor, setDoctor] = useState<DoctorCheck[] | null>(null);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [gates, setGates] = useState<ReleaseReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    Api.diagnostics().then(setDiag).catch((e) => setErr(String(e.message || e)));
    Api.doctor(false).then((d) => setDoctor(d.checks)).catch(() => {});
    Api.storage().then(setStorage).catch(() => {});
    Api.gatesReport().then(setGates).catch(() => setGates(null));
  };
  useEffect(load, []);

  const runGates = async () => {
    setErr(null); setFlash(null); setBusy(true);
    try {
      const r = await Api.gatesRun();
      setGates(r);
      setFlash(`release gates executed — qualification: ${r.verdict} (report written to .modelproof/release/)`);
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  return (
    <>
      <ErrorBox error={err} />
      {flash && <div className="note mt" style={{ color: "var(--accent)" }}>{flash}</div>}

      {diag && (
        <Card title="System information">
          <KV rows={[
            ["Engine", `v${diag.engine_version}`],
            ["Python", diag.python],
            ["Platform", diag.platform],
            ["Database", `${diag.database.path} · schema ${diag.database.schema_version ?? "?"}`],
            ["Records", Object.entries(diag.database.tables).map(([k, v]) => `${k}=${v}`).join(" · ")],
            ["Sandbox", diag.sandbox.docker_available ? "docker available" : `docker unavailable (${diag.sandbox.detail}) — ${diag.sandbox.host_execution_fallback}`],
            ["Inference", diag.inference.note],
            ["Telemetry", diag.telemetry],
          ]} />
        </Card>
      )}

      <Card title="Environment checks (doctor)" actions={
        <button className="ghost" onClick={() => Api.doctor(false).then((d) => setDoctor(d.checks)).catch(() => {})}>Re-run</button>
      }>
        {!doctor ? <Loading /> : (
          <table>
            <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {doctor.map((c) => (
                <tr key={c.name}>
                  <td className="mono">{c.name}</td>
                  <td><Badge value={c.ok ? "PASS" : "WARN"}>{c.label}</Badge></td>
                  <td className="note">{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Storage">
        {!storage ? <Loading /> : (
          <>
            <KV rows={[
              ["Artifacts (evidence)", `${fmtBytes(storage.artifacts_bytes)} across ${storage.run_count} runs`],
              ["Evidence bundles", fmtBytes(storage.bundles_bytes)],
              ["Database", fmtBytes(storage.db_bytes)],
              ["Retention", storage.retention_policy],
            ]} />
            {storage.runs.length > 0 && (
              <table className="mt">
                <thead><tr><th>Run</th><th>Artifacts</th><th>Bytes</th></tr></thead>
                <tbody>
                  {storage.runs.slice(0, 10).map((r) => (
                    <tr key={r.run_id}>
                      <td className="mono">{r.run_id}</td><td>{r.files}</td><td className="mono">{fmtBytes(r.bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Card>

      <Card title="Release qualification"
            actions={<button className="primary" disabled={busy} onClick={runGates}>{busy ? "running gates…" : "Run release gates"}</button>}>
        {!gates ? (
          <div className="note">No release report yet. Running the gates takes a few seconds and exercises the
            full pipeline (fixture-based), all refusal paths, evidence round-trips, and the API.</div>
        ) : (
          <>
            <div className="mt" style={{ marginBottom: "0.6rem" }}>
              Qualification: <Badge value={gates.verdict} /> · generated {fmtTime(gates.generated_at)} · engine {gates.engine_version}
            </div>
            <table>
              <thead><tr><th>Gate</th><th>Title</th><th>Status</th><th>Detail</th></tr></thead>
              <tbody>
                {gates.gates.map((g) => (
                  <tr key={g.id}>
                    <td className="mono">{g.id}</td>
                    <td>{g.title}{g.mandatory ? <span className="note"> (mandatory)</span> : null}</td>
                    <td><GateBadge outcome={g.status} /></td>
                    <td className="note">{g.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="note mt">Full evidence: <code>.modelproof/release/report.json</code> · human copy:{" "}
              <code>release.html</code>. Gates are environment-scoped — this describes THIS machine.</div>
          </>
        )}
      </Card>
    </>
  );
}
