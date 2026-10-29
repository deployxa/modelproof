/** Screen A — Home & system health: readiness, registered models, suites,
 *  recent runs, failing/blocked gates, resource warnings. All real data. */
import { useEffect, useState } from "react";
import { Api, type Diagnostics, type DoctorCheck, type ModelInfo, type ReleaseReport, type RunRow, type SuiteInfo } from "../api";
import { Badge, Card, ErrorBox, KV, Loading, fmtTime, navigate } from "../ui";

export default function Home() {
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [doctor, setDoctor] = useState<DoctorCheck[] | null>(null);
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [suites, setSuites] = useState<SuiteInfo[] | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [gates, setGates] = useState<ReleaseReport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      Api.diagnostics(), Api.doctor(false), Api.runs(8),
      Api.suites(), Api.models(), Api.gatesReport().catch(() => null),
    ])
      .then(([d, doc, r, s, m, g]) => {
        if (!alive) return;
        setDiag(d); setDoctor(doc.checks); setRuns(r.runs); setSuites(s.suites); setModels(m.models);
        if (g) setGates(g);
      })
      .catch((e) => alive && setErr(String(e.message || e)));
    const t = setInterval(() => { Api.runs(8).then((r) => alive && setRuns(r.runs)).catch(() => {}); }, 2500);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const failingRuns = runs?.filter((r) => r.verdict === "FAIL" || r.verdict === "INVALID") ?? [];
  const active = runs?.some((r) => r.status === "QUEUED" || r.status === "RUNNING") ?? false;

  return (
    <>
      <ErrorBox error={err} />
      {diag && (
        <Card title="System readiness">
          <KV rows={[
            ["Engine", <>{`v${diag.engine_version}`} <Badge value="PASS">ready</Badge></>],
            ["Python / platform", `${diag.python} · ${diag.platform}`],
            ["Database", `${diag.database.path} (schema ${diag.database.schema_version ?? "?"})`],
            ["Sandbox", diag.sandbox.docker_available
              ? <Badge value="PASS">docker available</Badge>
              : <Badge value="BLOCKED">docker unavailable — sandbox runs refused by design (no host fallback)</Badge>],
            ["Inference", diag.inference.note],
            ["Telemetry", diag.telemetry],
          ]} />
        </Card>
      )}
      {doctor && doctor.some((c) => !c.ok) && (
        <Card title="Doctor findings">
          <table><tbody>
            {doctor.filter((c) => !c.ok).map((c) => (
              <tr key={c.name}><td style={{ width: 180 }}><Badge value={c.ok ? "PASS" : "WARN"}>{c.label}</Badge></td>
                <td className="mono">{c.name}</td><td className="note">{c.detail}</td></tr>
            ))}
          </tbody></table>
        </Card>
      )}

      <div className="grid cols3">
        <Card title="Registered models">
          <div className="stat"><span className="num">{models?.length ?? "…"}</span>
            <span className="lbl">{models?.filter((m) => m.identity_verified).length ?? 0} with verified weights manifest</span></div>
          <button className="ghost mt" onClick={() => navigate("#/models")}>Open model registry</button>
        </Card>
        <Card title="Evaluation suites">
          <div className="stat"><span className="num">{suites?.length ?? "…"}</span>
            <span className="lbl">{suites?.[0]?.provenance_status.replace(/_/g, " ").toLowerCase() ?? "no suites registered"}</span></div>
          <button className="ghost mt" onClick={() => navigate("#/suites")}>Inspect suites</button>
        </Card>
        <Card title="Integrity posture">
          <div className="stat"><span className="num">{failingRuns.length}</span>
            <span className="lbl">runs with failing integrity/execution gates · {runs?.filter((r) => r.is_fixture).length ?? 0} fixture run(s)</span></div>
          {gates ? (
            <div className="note mt">
              Release qualification: <Badge value={gates.verdict} />
            </div>
          ) : (
            <div className="note mt">No release-gate report yet — run <code>modelproof gates run</code> or trigger it from Settings.</div>
          )}
        </Card>
      </div>

      <Card title={<>Recent evaluation runs {active && <Badge value="RUNNING">activity</Badge>}</>}>
        {runs === null ? <Loading /> : runs.length === 0 ? (
          <div className="note">No runs yet. Register a model, then start an evaluation from the Runs screen.</div>
        ) : (
          <table>
            <thead><tr><th>Run</th><th>Model</th><th>Suite</th><th>Status</th><th>Verdict</th><th>Started</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => navigate(`#/runs/${r.id}`)}>
                  <td className="mono">{r.id}{r.is_fixture ? " ⚑" : ""}{r.imported_from ? " ⇦" : ""}</td>
                  <td>{r.model_name}</td>
                  <td className="mono">{r.suite_id} v{r.suite_version}</td>
                  <td><Badge value={r.status} /></td>
                  <td>{r.verdict ? <Badge value={r.verdict} /> : "—"}</td>
                  <td className="note">{fmtTime(r.started_at ?? r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="note mt">⚑ fixture run · ⇦ imported evidence bundle. A PASS verdict certifies run integrity — never model capability.</div>
      </Card>
    </>
  );
}
