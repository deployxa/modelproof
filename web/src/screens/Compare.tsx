/** Screen F (comparisons) — paired model comparison with validity gating.
 *  Incompatible runs produce a refusal with reasons, never a ranking. */
import { useEffect, useState } from "react";
import { Api, type CompareResult, type RunRow } from "../api";
import { Card, ErrorBox, KV, fmtPct } from "../ui";

export default function Compare() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Api.runs(100).then((d) => {
      setRuns(d.runs);
      const completed = d.runs.filter((r) => r.status === "COMPLETED");
      if (completed[0]) setA((x) => x || completed[0].id);
      if (completed[1]) setB((x) => x || completed[1].id);
    }).catch((e) => setErr(String(e.message || e)));
  }, []);

  const run = async () => {
    setErr(null); setBusy(true); setResult(null);
    try { setResult(await Api.compare(a, b)); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  return (
    <>
      <ErrorBox error={err} />
      <Card title="Paired comparison (same tasks, same protocol — enforced)">
        <div className="grid cols2">
          <label className="field"><span>Run A</span>
            <select value={a} onChange={(e) => setA(e.target.value)}>
              <option value="">— choose —</option>
              {runs?.filter((r) => r.status === "COMPLETED").map((r) => (
                <option key={r.id} value={r.id}>{r.id} · {r.model_name} @ {r.suite_id} v{r.suite_version}{r.is_fixture ? " ⚑fixture" : ""}</option>
              ))}
            </select></label>
          <label className="field"><span>Run B</span>
            <select value={b} onChange={(e) => setB(e.target.value)}>
              <option value="">— choose —</option>
              {runs?.filter((r) => r.status === "COMPLETED").map((r) => (
                <option key={r.id} value={r.id}>{r.id} · {r.model_name} @ {r.suite_id} v{r.suite_version}{r.is_fixture ? " ⚑fixture" : ""}</option>
              ))}
            </select></label>
        </div>
        <button className="primary" disabled={!a || !b || a === b || busy} onClick={run}>
          {busy ? "computing…" : "Compare"}
        </button>
        {a === b && <div className="note mt">Pick two different runs.</div>}
      </Card>

      {result && (
        <>
          {!result.compatible ? (
            <Card title="Comparison refused">
              <div className="error-panel">{result.refusal}</div>
              <h3 style={{ margin: "0.8rem 0 0.4rem" }}>Incompatibilities</h3>
              <ul className="note">{result.incompatibilities?.map((i) => <li key={i} className="mono">{i}</li>)}</ul>
              <div className="note mt">Comparisons are only computed when suite identity+version+content hash,
                generation settings, execution mode, and prompt template all match — incompatible protocols
                produce misleading rankings, so they are refused outright.</div>
            </Card>
          ) : (
            <>
              <div className="grid cols3">
                <Card title="Pass rates (paired tasks)">
                  <KV rows={[
                    ["Run A", fmtPct(result.pass_rate_a)],
                    ["Run B", fmtPct(result.pass_rate_b)],
                    ["Paired tasks", result.n_paired_tasks],
                    ["Excluded (missing outcomes)", result.n_excluded_tasks],
                  ]} />
                </Card>
                <Card title="Discordant pairs">
                  <KV rows={[
                    ["A passed, B failed", result.discordant?.a_passed_b_failed],
                    ["B passed, A failed", result.discordant?.a_failed_b_passed],
                    ["Both passed", result.both_passed],
                    ["Both failed", result.both_failed],
                  ]} />
                </Card>
                <Card title="Exact McNemar test">
                  <div className="stat">
                    <span className="num">{result.mcnemar_exact_p == null ? "—" : `p = ${result.mcnemar_exact_p.toFixed(4)}`}</span>
                    <span className="lbl">two-sided, exact binomial on discordant pairs</span>
                  </div>
                </Card>
              </div>
              <Card title="Paired bootstrap CI — difference in pass rates (A − B)">
                {result.bootstrap?.ci ? (
                  <KV rows={[
                    ["95% CI", `[${(result.bootstrap.ci[0] * 100).toFixed(1)}%, ${(result.bootstrap.ci[1] * 100).toFixed(1)}%]`],
                    ["Point estimate", fmtPct(result.bootstrap.point_estimate)],
                    ["Resamples", `${result.bootstrap.n_resamples} (seeded, deterministic)`],
                  ]} />
                ) : <div className="note">no paired tasks</div>}
              </Card>
              <Card title="Read this before quoting any number above">
                <ul className="note" style={{ paddingLeft: "1.2rem", margin: 0 }}>
                  {result.caveats?.map((c) => <li key={c} style={{ marginBottom: "0.3rem" }}>{c}</li>)}
                  {result.compatibility?.warnings?.map((w) => <li key={w} style={{ marginBottom: "0.3rem" }}>⚠ {w}</li>)}
                </ul>
              </Card>
            </>
          )}
        </>
      )}
    </>
  );
}
