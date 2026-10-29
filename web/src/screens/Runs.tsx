/** Screens D + E — Evaluation configuration and live run monitoring.
 *  Configuration validates before submission; monitoring distinguishes real
 *  progress from estimates and never blocks the UI on benchmark completion. */
import { useEffect, useState } from "react";
import { Api, type ModelInfo, type RunRow } from "../api";
import { Badge, Card, ErrorBox, Loading, StatusBadge, fmtTime, navigate } from "../ui";

export default function Runs() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [modelName, setModelName] = useState("");
  const [suitePath, setSuitePath] = useState("suites/python-core");
  const [mode, setMode] = useState("fixture_replay");
  const [fixturePath, setFixturePath] = useState("examples/fixture-outputs.json");
  const [taskIds, setTaskIds] = useState("");

  const load = () => Api.runs(100).then((d) => setRuns(d.runs)).catch((e) => setErr(String(e.message || e)));
  useEffect(() => {
    load();
    Api.models().then((d) => { setModels(d.models); if (d.models[0]) setModelName((n) => n || d.models[0].name); }).catch(() => {});
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, []);

  const submit = async () => {
    setErr(null); setFlash(null); setSubmitting(true);
    try {
      const ids = taskIds.split(",").map((s) => s.trim()).filter(Boolean);
      const r = await Api.createRun({
        model_name: modelName,
        suite_path: suitePath,
        execution_mode: mode,
        task_ids: ids.length ? ids : null,
        fixture_outputs_path: mode === "fixture_replay" ? fixturePath : null,
      });
      setFlash(`run ${r.run_id} queued (${r.is_fixture ? "FIXTURE — labeled, never benchmark evidence" : "sandbox mode"})`);
      await load();
      navigate(`#/runs/${r.run_id}`);
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setSubmitting(false); }
  };

  const active = runs?.filter((r) => r.status === "QUEUED" || r.status === "RUNNING") ?? [];

  return (
    <>
      <ErrorBox error={err} />
      {flash && <div className="note mt" style={{ color: "var(--accent)" }}>{flash}</div>}

      {active.length > 0 && (
        <Card title="Active runs">
          {active.map((r) => (
            <div key={r.id} className="mt" style={{ display: "flex", gap: "0.8rem", alignItems: "center" }}>
              <Badge value={r.status} /> <span className="mono">{r.id}</span>
              <span className="note">{r.model_name} @ {r.suite_id}</span>
              <button className="ghost" style={{ marginLeft: "auto" }} onClick={async () => {
                try { await Api.cancelRun(r.id); setFlash(`cancellation requested for ${r.id} — honoured between cases`); await load(); }
                catch (e) { setErr(String((e as Error).message)); }
              }}>Cancel</button>
              <button className="ghost" onClick={() => navigate(`#/runs/${r.id}`)}>Monitor</button>
            </div>
          ))}
          <div className="note mt">The worker honours cancellation between cases; partial runs become INCOMPLETE, never silently completed.</div>
        </Card>
      )}

      <Card title="Configure an evaluation">
        <div className="grid cols2">
          <label className="field"><span>Model (registered, loopback endpoint)</span>
            <select value={modelName} onChange={(e) => setModelName(e.target.value)}>
              <option value="">— choose model —</option>
              {models?.map((m) => <option key={m.name} value={m.name}>{m.name}{m.identity_verified ? " ✓weights" : " (identity unverified)"}</option>)}
            </select></label>
          <label className="field"><span>Suite directory (validated again at run creation)</span>
            <input type="text" value={suitePath} onChange={(e) => setSuitePath(e.target.value)} /></label>
          <label className="field"><span>Execution mode</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="sandbox">sandbox — real execution, requires Docker (refused otherwise)</option>
              <option value="fixture_replay">fixture_replay — labeled demonstration, never evidence</option>
            </select></label>
          <label className="field"><span>Fixture outputs JSON (fixture mode only)</span>
            <input type="text" value={fixturePath} onChange={(e) => setFixturePath(e.target.value)} disabled={mode !== "fixture_replay"} /></label>
          <label className="field"><span>Task subset (optional, comma-separated ids — empty = whole suite)</span>
            <input type="text" value={taskIds} onChange={(e) => setTaskIds(e.target.value)} placeholder="bf-leap-year, eb-strict-int-parse" /></label>
        </div>
        {mode === "sandbox" && (
          <div className="warnbox">Sandbox mode runs generated code in a network-disabled Docker container. If
            Docker is unavailable, the run is REFUSED — generated code is never executed on the host.</div>
        )}
        {mode === "fixture_replay" && (
          <div className="warnbox">Fixture replay executes nothing and contacts no model: it replays
            recorded dev-side outcomes to exercise the pipeline. Fixture runs are permanently labeled and
            capped at INSUFFICIENT_EVIDENCE.</div>
        )}
        <button className="primary" disabled={!modelName || !suitePath || submitting} onClick={submit}>
          {submitting ? "creating…" : "Create + queue run"}
        </button>
        <div className="note mt">Effective generation parameters (temperature, max_tokens…) come from the model's
          registry record and are frozen into the run configuration hash at creation.</div>
      </Card>

      <Card title="Run history">
        {runs === null ? <Loading /> : runs.length === 0 ? (
          <div className="note">No runs yet.</div>
        ) : (
          <table>
            <thead><tr><th>Run</th><th>Model</th><th>Suite</th><th>Status</th><th>Verdict</th><th>Created</th><th>Ended</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => navigate(`#/runs/${r.id}`)}>
                  <td className="mono">{r.id}{r.is_fixture ? " ⚑" : ""}{r.imported_from ? " ⇦" : ""}</td>
                  <td>{r.model_name}</td>
                  <td className="mono">{r.suite_id} v{r.suite_version}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td>{r.verdict ? <Badge value={r.verdict} /> : "—"}</td>
                  <td className="note">{fmtTime(r.created_at)}</td>
                  <td className="note">{fmtTime(r.ended_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
