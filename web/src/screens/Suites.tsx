/** Screen C — Benchmark management: suite list + versions, validate a suite
 *  directory, inspect tasks, provenance + leakage, changelog, cross-suite
 *  overlap. Private-test CONTENTS are never displayed (names/metadata only). */
import { useEffect, useState } from "react";
import { Api, type SuiteInfo, type SuiteValidation } from "../api";
import { Badge, Card, ErrorBox, KV, Loading, fmtTime } from "../ui";

export default function Suites() {
  const [suites, setSuites] = useState<SuiteInfo[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [path, setPath] = useState("suites/python-core");
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<SuiteValidation | null>(null);
  const [changelogFor, setChangelogFor] = useState<string | null>(null);
  const [changelog, setChangelog] = useState<{ versions: { version: string; content_hash: string; path: string; registered_at: string }[] } | null>(null);
  // overlap form
  const [ovA, setOvA] = useState("suites/python-core");
  const [ovB, setOvB] = useState("suites/python-core");
  const [overlap, setOverlap] = useState<Awaited<ReturnType<typeof Api.suiteOverlap>> | null>(null);
  const [overlapBusy, setOverlapBusy] = useState(false);

  const load = () => Api.suites().then((d) => setSuites(d.suites)).catch((e) => setErr(String(e.message || e)));
  useEffect(() => { load(); }, []);

  const validate = async (alsoRegister: boolean) => {
    setErr(null); setFlash(null); setValidating(true); setValidation(null);
    try {
      const r = await Api.suiteValidate(path, alsoRegister);
      setValidation(r);
      if (alsoRegister) { setFlash(`suite ${r.suite_id} v${r.version} registered`); await load(); }
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setValidating(false); }
  };

  const showChangelog = async (id: string) => {
    setErr(null);
    try {
      const r = await Api.suiteChangelog(id);
      setChangelogFor(id); setChangelog(r);
    } catch (e) { setErr(String((e as Error).message)); }
  };

  const checkOverlap = async () => {
    setErr(null); setOverlapBusy(true); setOverlap(null);
    try { setOverlap(await Api.suiteOverlap(ovA, ovB)); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setOverlapBusy(false); }
  };

  return (
    <>
      <ErrorBox error={err} />
      {flash && <div className="note mt" style={{ color: "var(--accent)" }}>{flash}</div>}

      <Card title="Registered suites">
        {suites === null ? <Loading /> : suites.length === 0 ? (
          <div className="note">No suites registered. Validate the bundled suite below to register it.</div>
        ) : (
          <table>
            <thead><tr><th>Suite</th><th>Version</th><th>Tasks</th><th>Provenance</th><th>Content hash</th><th>Leakage</th><th></th></tr></thead>
            <tbody>
              {suites.map((s) => (
                <tr key={`${s.suite_id}@${s.version}`} style={s.error ? { color: "var(--red)" } : undefined}>
                  <td className="mono">{s.suite_id}</td>
                  <td className="mono">{s.version}</td>
                  <td>{s.error ? "—" : s.tasks}
                    {!s.error && <div className="note">{s.categories?.join(", ")}</div>}</td>
                  <td>{s.error ? "?" : <Badge value="NOT_RUN">{s.provenance_status.replace(/_/g, " ").toLowerCase()}</Badge>}</td>
                  <td className="mono note">{s.content_hash ? `${s.content_hash.slice(0, 16)}…` : "—"}</td>
                  <td className="note">{s.error ? s.error : s.leakage}</td>
                  <td><button className="ghost" onClick={() => showChangelog(s.suite_id)}>Changelog</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {changelog && changelogFor && (
          <div className="mt">
            <h3 style={{ margin: "0 0 .4rem" }}>Changelog — {changelogFor}</h3>
            <table>
              <thead><tr><th>Version</th><th>Content hash</th><th>Registered</th><th>Path</th></tr></thead>
              <tbody>
                {changelog.versions.map((v) => (
                  <tr key={v.version + v.registered_at}>
                    <td className="mono">{v.version}</td>
                    <td className="mono note">{v.content_hash.slice(0, 20)}…</td>
                    <td className="note">{fmtTime(v.registered_at)}</td>
                    <td className="mono note">{v.path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="note mt">Suite updates create NEW immutable versions — historical results stay interpretable against the version they were produced with.</div>
          </div>
        )}
      </Card>

      <Card title="Validate a suite directory">
        <div className="grid cols2">
          <label className="field"><span>Suite directory (absolute or relative to the engine's working directory)</span>
            <input type="text" value={path} onChange={(e) => setPath(e.target.value)} /></label>
        </div>
        <button className="primary" disabled={validating || !path} onClick={() => validate(false)}>
          {validating ? "validating…" : "Validate"}
        </button>{" "}
        <button className="ghost" disabled={validating || !path} onClick={() => validate(true)}>Validate + register</button>
        {validation && (
          <div className="mt">
            <KV rows={[
              ["Suite", `${validation.suite_id} v${validation.version}`],
              ["Content hash", validation.content_hash],
              ["Provenance", <Badge value="NOT_RUN">{validation.provenance_status}</Badge>],
              ["Leakage", validation.leakage],
            ]} />
            <h3 style={{ margin: "0.8rem 0 0.4rem" }}>Tasks ({validation.tasks.length})</h3>
            <table>
              <thead><tr><th>ID</th><th>Title</th><th>Category</th><th>Timeout</th></tr></thead>
              <tbody>
                {validation.tasks.map((t) => (
                  <tr key={t.id}>
                    <td className="mono">{t.id}</td><td>{t.title}</td>
                    <td className="mono note">{t.category}</td><td className="note">{t.timeout_seconds}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="warnbox mt">
              <b>What these checks establish — and what they do not.</b> Performed: {validation.checks_performed.join("; ")}.
              Not performed: {validation.checks_not_performed.join("; ")}. A clean scan never establishes
              independence from any model's training corpus.
            </div>
          </div>
        )}
      </Card>

      <Card title="Cross-suite overlap check">
        <div className="grid cols2">
          <label className="field"><span>Suite A (directory or registered id)</span>
            <input type="text" value={ovA} onChange={(e) => setOvA(e.target.value)} /></label>
          <label className="field"><span>Suite B (directory or registered id)</span>
            <input type="text" value={ovB} onChange={(e) => setOvB(e.target.value)} /></label>
        </div>
        <button className="primary" disabled={overlapBusy || !ovA || !ovB} onClick={checkOverlap}>
          {overlapBusy ? "checking…" : "Check overlap"}
        </button>
        {overlap && (
          <div className="mt">
            <KV rows={[
              ["Status", <Badge value={overlap.status === "POTENTIAL_OVERLAP_FOUND" ? "WARN" : "PASS"}>{overlap.status}</Badge>],
              ["Tasks compared", `${overlap.tasks_compared[0]} × ${overlap.tasks_compared[1]}`],
              ["Exact hash overlaps", overlap.exact_hash_overlaps.length ? overlap.exact_hash_overlaps.map(([a, b]) => `${a} ↔ ${b}`).join(", ") : "none"],
              ["Normalized overlaps", overlap.normalized_overlaps.length ? overlap.normalized_overlaps.map(([a, b]) => `${a} ↔ ${b}`).join(", ") : "none"],
              ["Near-dup candidates", overlap.near_dup_candidates.length ? overlap.near_dup_candidates.map(([a, b, r]) => `${a} ↔ ${b} (${r})`).join(", ") : "none"],
            ]} />
            <div className="note mt">{overlap.note} Not performed: {overlap.checks_not_performed.join("; ")}.</div>
          </div>
        )}
      </Card>
    </>
  );
}
