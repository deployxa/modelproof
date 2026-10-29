/** Screens E + F + G — Run monitoring, results/analysis, and evidence explorer
 *  for one run. Tabs: Monitor · Results · Cases · Evidence · Gates. */
import { useEffect, useState } from "react";
import { Api, type BundleVerify, type CaseRow, type EventRow, type EvidenceExportResult, type GateRow, type RunDetailT, type Summary, type VerifyResult } from "../api";
import { Badge, Card, ErrorBox, GateBadge, KV, Loading, StatusBadge, fmtBytes, fmtPct, fmtTime, navigate } from "../ui";

type Tab = "monitor" | "results" | "cases" | "evidence" | "gates";

export default function RunDetail({ runId, initialTab }: { runId: string; initialTab?: Tab }) {
  const [detail, setDetail] = useState<RunDetailT | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab ?? "monitor");
  const [err, setErr] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseRow[] | null>(null);
  const [events, setEvents] = useState<EventRow[] | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => Api.run(runId).then((d) => { if (alive) setDetail(d); }).catch((e) => alive && setErr(String(e.message || e)));
    load();
    const t = setInterval(() => {
      Api.run(runId).then((d) => {
        if (!alive) return;
        setDetail(d);
        if (d.status === "QUEUED" || d.status === "RUNNING") {
          Api.runEvents(runId).then((e) => alive && setEvents(e.events)).catch(() => {});
        }
      }).catch(() => {});
    }, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [runId]);

  useEffect(() => {
    if (tab === "cases" && !cases && detail) {
      Api.runCases(runId).then((d) => setCases(d.cases)).catch((e) => setErr(String(e.message || e)));
    }
    if (tab === "monitor" && !events && detail) {
      Api.runEvents(runId).then((d) => setEvents(d.events)).catch(() => {});
    }
  }, [tab, detail, runId, cases, events]);

  if (err) return <ErrorBox error={err} />;
  if (!detail) return <Loading label="loading run" />;

  const s: Summary | undefined = detail.summary;
  const tabs: [Tab, string][] = [
    ["monitor", "Monitor"], ["results", "Results"], ["cases", "Cases"], ["evidence", "Evidence"], ["gates", "Gates"],
  ];

  return (
    <>
      <Card title={<><span className="mono">{detail.id}</span></>}
            actions={<button className="ghost" onClick={() => navigate("#/runs")}>← all runs</button>}>
        <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap" }}>
          <StatusBadge status={detail.status} />
          {detail.verdict ? <Badge value={detail.verdict} /> : <span className="note">no verdict yet</span>}
          {detail.is_fixture ? <Badge value="WARN">FIXTURE</Badge> : null}
          {detail.imported_from ? <Badge value="NOT_RUN">IMPORTED BUNDLE</Badge> : null}
          <span className="note">{detail.model_name} @ {detail.suite_id} v{detail.suite_version}</span>
        </div>
        {detail.is_fixture && (
          <div className="fixture-banner mt">
            FIXTURE RUN — outcomes replayed from recorded dev-side fixtures. No model ran and no code
            executed. This run demonstrates the pipeline and supports no claim about any model.
          </div>
        )}
        {detail.imported_from && (
          <div className="warnbox mt">
            IMPORTED — this run was NOT executed on this machine. Evidence integrity was verified at import
            time; the original run id is <code>{JSON.parse(detail.imported_from).original_run_id ?? "?"}</code>.
          </div>
        )}
        {detail.error && <div className="error-panel mt">{detail.error}</div>}
      </Card>

      <div className="tabs-row">
        {tabs.map(([id, label]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {tab === "monitor" && <MonitorTab detail={detail} events={events} onRefresh={async () => setDetail(await Api.run(runId))} />}
      {tab === "results" && <ResultsTab summary={s} />}
      {tab === "cases" && <CasesTab cases={cases} />}
      {tab === "evidence" && <EvidenceTab runId={runId} detail={detail} />}
      {tab === "gates" && <GatesTab gates={detail.gates} summary={s} />}
    </>
  );
}

/* ------------------------------------------------------------------ monitor */
function MonitorTab({ detail, events, onRefresh }: { detail: RunDetailT; events: EventRow[] | null; onRefresh: () => void }) {
  const s = detail.summary;
  const done = s ? s.cases_terminal : null;
  const total = s ? s.tasks_total : null;
  const pct = done != null && total ? Math.round((done / total) * 100) : null;
  return (
    <>
      <div className="grid cols3">
        <Card title="Lifecycle">
          <KV rows={[
            ["Status", <StatusBadge status={detail.status} />],
            ["Created", fmtTime(detail.created_at)],
            ["Started", fmtTime(detail.started_at)],
            ["Ended", fmtTime(detail.ended_at)],
            ["Error", detail.error ?? "—"],
          ]} />
        </Card>
        <Card title="Progress (actual, not estimated)">
          <div className="stat">
            <span className="num">{done ?? 0} / {total ?? "?"}</span>
            <span className="lbl">terminal case outcomes</span>
          </div>
          <div className="progress"><div style={{ width: `${pct ?? 0}%` }} /></div>
          {s && <div className="note mt">completion rate {fmtPct(s.completion_rate)} (denominator: all suite tasks)</div>}
        </Card>
        <Card title="Controls">
          <button className="ghost" onClick={onRefresh}>Refresh now</button>
          <div className="note mt">Cancellation is available on the Runs screen while the run is queued/active.
            Interrupted runs recover to INCOMPLETE on engine start and can be resumed from the CLI
            (<code>modelproof run resume {detail.id}</code>).</div>
        </Card>
      </div>
      <Card title="Event history">
        {events === null ? <Loading /> : (
          <table>
            <thead><tr><th style={{ width: 190 }}>Time</th><th style={{ width: 70 }}>Level</th><th>Event</th></tr></thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i}>
                  <td className="note mono">{e.at.slice(11, 23)}</td>
                  <td><Badge value={e.level === "error" ? "FAIL" : e.level === "warn" ? "WARN" : "PASS"}>{e.level}</Badge></td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ results */
function ResultsTab({ summary: s }: { summary?: Summary }) {
  if (!s) return <div className="note">No results yet — the run has not finished (or failed before finalization).</div>;
  const cats = Object.entries(s.category_pass_rates || {});
  return (
    <>
      {s.is_fixture && (
        <div className="fixture-banner">All numbers below come from a FIXTURE replay — they are pipeline
          demonstrations, not benchmark results.</div>
      )}
      <div className="grid cols3">
        <Card title="Task pass rate">
          <div className="stat">
            <span className="num">{fmtPct(s.pass_rate)}</span>
            <span className="lbl">{s.pass_rate_numerator}/{s.pass_rate_denominator} valid outcomes (infrastructure failures excluded from denominator, never hidden)</span>
          </div>
          {s.wilson95 && <div className="note mt">Wilson 95% CI: [{(s.wilson95[0] * 100).toFixed(1)}%, {(s.wilson95[1] * 100).toFixed(1)}%]</div>}
        </Card>
        <Card title="Full-suite completion">
          <div className="stat"><span className="num">{fmtPct(s.completion_rate)}</span>
            <span className="lbl">{s.cases_terminal}/{s.tasks_total} tasks with terminal outcomes</span></div>
        </Card>
        <Card title="Outcome breakdown">
          <KV rows={[
            ["passed", s.passed], ["failed", s.failed], ["timeouts", s.timeouts],
            ["invalid output", s.invalid_outputs], ["model errors", s.model_errors],
            ["sandbox errors", s.sandbox_errors],
          ]} />
        </Card>
      </div>
      <Card title="Category breakdown">
        {cats.length === 0 ? <div className="note">—</div> : (
          <table>
            <thead><tr><th>Category</th><th>Pass rate</th></tr></thead>
            <tbody>{cats.map(([c, r]) => (
              <tr key={c}><td className="mono">{c}</td><td>{fmtPct(r)}</td></tr>
            ))}</tbody>
          </table>
        )}
        <div className="note mt">Category rates with a zero denominator are reported as 0 but are not real
          measurements — check the outcome breakdown before reading them.</div>
      </Card>
      <Card title="Verdict">
        <div style={{ marginBottom: "0.5rem" }}><Badge value={s.verdict} /></div>
        <div>{s.verdict_explanation}</div>
        <div className="note mt">
          A PASS certifies run integrity ONLY — not model capability, not benchmark independence, not
          production readiness.
        </div>
      </Card>
      <Card title="Frozen configuration">
        <KV rows={[
          ["config hash", s.config_hash],
          ["suite content hash", s.suite_content_hash],
          ["prompt template hash", s.prompt_template_hash],
          ["provenance status", s.provenance_status],
          ["leakage", s.leakage ?? "—"],
          ["environment", s.environment ? `python ${s.environment.python} · engine ${s.environment.modelproof_version}` : "—"],
        ]} />
      </Card>
    </>
  );
}

/* -------------------------------------------------------------------- cases */
function CasesTab({ cases }: { cases: CaseRow[] | null }) {
  if (!cases) return <Loading />;
  if (cases.length === 0) return <div className="note">No case records (run not started or imported bundle without case artifacts).</div>;
  return (
    <Card title={`Per-task outcomes (${cases.length})`}>
      <table>
        <thead><tr>
          <th>Task</th><th>Category</th><th>Status</th><th>Checks</th><th>Duration</th><th>Failure detail</th>
        </tr></thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.task_id}>
              <td className="mono">{c.task_id}{c.is_resumed ? <span className="note"> (resumed)</span> : null}</td>
              <td className="mono note">{c.category}</td>
              <td><Badge value={c.status} />{c.is_infrastructure_failure ? <div className="note">infrastructure</div> : null}</td>
              <td className="mono">{c.checks_passed}/{c.checks_total}</td>
              <td className="mono">{c.duration_ms != null ? `${c.duration_ms} ms` : "—"}</td>
              <td className="note">{c.failed_checks?.length ? `failed: ${c.failed_checks.join(", ")}` : (c.error_text?.split("\n")[0] ?? "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="note mt">Private acceptance-test contents are never shown — only check names and truncated first-line errors.</div>
    </Card>
  );
}

/* ------------------------------------------------------------------ evidence */
function EvidenceTab({ runId, detail }: { runId: string; detail: RunDetailT }) {
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [bundle, setBundle] = useState<EvidenceExportResult | null>(null);
  const [bundleVerify, setBundleVerify] = useState<BundleVerify | null>(null);
  const [importPath, setImportPath] = useState("");
  const [importAs, setImportAs] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const doVerify = async () => {
    setErr(null); setFlash(null); setBusy("verify");
    try { setVerify(await Api.verifyRun(runId)); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };
  const doExport = async () => {
    setErr(null); setFlash(null); setBusy("export");
    try { setBundle(await Api.evidenceExport(runId)); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };
  const doVerifyBundle = async () => {
    setErr(null); setFlash(null); setBusy("bundle-verify");
    try {
      const path = bundle?.bundle_path || importPath;
      if (!path) throw new Error("export the bundle first or enter a bundle path");
      setBundleVerify(await Api.evidenceVerify(path));
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };
  const doImport = async () => {
    setErr(null); setFlash(null); setBusy("import");
    try {
      const r = await Api.evidenceImport(importPath, importAs || undefined);
      setFlash(`imported as ${r.run_id} (original ${r.original_run_id}), ${r.checked} artifacts verified`);
      navigate(`#/runs/${r.run_id}`);
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };

  return (
    <>
      <ErrorBox error={err} />
      {flash && <div className="note mt" style={{ color: "var(--accent)" }}>{flash}</div>}
      <div className="grid cols2">
        <Card title="Artifact integrity (local)">
          <button className="primary" disabled={busy === "verify"} onClick={doVerify}>
            {busy === "verify" ? "verifying…" : "Recompute all artifact hashes"}
          </button>
          {verify && (
            <div className="mt">
              <KV rows={[
                ["Verified", verify.verified ? <Badge value="PASS">all {verify.checked_artifacts} artifacts match</Badge>
                  : <Badge value="FAIL">integrity failure</Badge>],
                ["Missing", verify.missing.length ? verify.missing.join(", ") : "none"],
                ["Mismatched", verify.mismatched.length ? verify.mismatched.join(", ") : "none"],
              ]} />
              {verify.checks.map((c) => (
                <div key={c.gate} className="note mt">[{c.ok ? "PASS" : "FAIL"}] {c.gate}: {c.detail}</div>
              ))}
            </div>
          )}
          <div className="note mt">Verification recomputes every SHA-256 from disk and the frozen config digest. A
            valid checksum proves file integrity only — not that the run happened as described.</div>
        </Card>
        <Card title="Portable evidence bundle">
          <button className="primary" disabled={busy === "export" || !detail.summary} onClick={doExport}>
            {busy === "export" ? "exporting…" : "Export evidence bundle"}
          </button>
          {bundle && (
            <div className="note mt">
              bundle: <code>{bundle.bundle_path}</code> ({fmtBytes(bundle.size_bytes)})
            </div>
          )}
          <div className="mt" style={{ display: "flex", gap: "0.5rem" }}>
            <button className="ghost" disabled={busy === "bundle-verify"} onClick={doVerifyBundle}>
              {busy === "bundle-verify" ? "verifying…" : "Verify bundle"}
            </button>
            <input type="text" style={{ flex: 1 }} placeholder="bundle path (defaults to the export above)"
                   value={bundle?.bundle_path && !importPath ? bundle.bundle_path : importPath}
                   onChange={(e) => setImportPath(e.target.value)} />
          </div>
          {bundleVerify && (
            <div className="mt">
              <KV rows={[
                ["Bundle", bundleVerify.verified ? <Badge value="PASS">{bundleVerify.checked} artifacts verified</Badge>
                  : <Badge value="FAIL">verification failed</Badge>],
                ["Run", bundleVerify.run_id],
                ["Schema", bundleVerify.bundle_schema],
              ]} />
            </div>
          )}
          <div className="warnbox mt" style={{ marginTop: "0.8rem" }}>
            Import a bundle from another machine (verify-before-import is enforced):
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
              <input type="text" style={{ flex: 1 }} placeholder="/path/to/run.bundle.tar.gz"
                     value={importPath} onChange={(e) => setImportPath(e.target.value)} />
              <input type="text" style={{ width: 150 }} placeholder="import as (optional)"
                     value={importAs} onChange={(e) => setImportAs(e.target.value)} />
              <button className="ghost" disabled={busy === "import" || !importPath} onClick={doImport}>
                {busy === "import" ? "importing…" : "Import"}
              </button>
            </div>
          </div>
          <div className="note mt">What a verified bundle proves: artifact file integrity. What it does NOT prove:
            that the recorded evaluation actually happened, statistical validity, model-identity claims, or the
            exporter's authenticity.</div>
        </Card>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------- gates */
function GatesTab({ gates, summary: s }: { gates: GateRow[]; summary?: Summary }) {
  if (!gates?.length) return <div className="note">No run gates recorded (imported bundles without gate artifacts, or run never finalized).</div>;
  return (
    <Card title="Run gates">
      <table>
        <thead><tr><th>Gate</th><th>Outcome</th><th>Detail</th><th>Evidence</th></tr></thead>
        <tbody>
          {gates.map((g) => (
            <tr key={g.gate}>
              <td className="mono">{g.gate}</td>
              <td><GateBadge outcome={g.outcome} /></td>
              <td>{g.detail}</td>
              <td className="note mono">{g.evidence_ref ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {s && <div className="note mt">Verdict: <Badge value={s.verdict} /> — {s.verdict_explanation}</div>}
    </Card>
  );
}
