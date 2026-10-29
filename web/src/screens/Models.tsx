/** Screen B — Model registry: register local models, inspect manifests,
 *  verify hashes, test endpoint connectivity, edit generation settings,
 *  show unverified metadata CLEARLY as declared-not-verified. */
import { useEffect, useState } from "react";
import { Api, type ModelInfo } from "../api";
import { Badge, Card, ErrorBox, KV, Loading, navigate } from "../ui";

export default function Models({ onRegistered }: { onRegistered?: () => void }) {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // register form
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:8080");
  const [weightsDir, setWeightsDir] = useState("");
  const [serverModelId, setServerModelId] = useState("");
  const [notes, setNotes] = useState("");

  const load = () => Api.models().then((d) => setModels(d.models)).catch((e) => setErr(String(e.message || e)));
  useEffect(() => { load(); }, []);

  const register = async () => {
    setErr(null); setFlash(null); setBusy("register");
    try {
      const r = await Api.registerModel({
        name, endpoint,
        weights_dir: weightsDir || undefined,
        server_model_id: serverModelId || undefined,
        notes: notes || undefined,
      });
      setFlash(`registered ${r.name}${r.identity_verified ? " with verified weights manifest" : " — artifact identity UNVERIFIED (no weights directory declared)"}`);
      setName(""); setWeightsDir(""); setServerModelId(""); setNotes("");
      await load(); onRegistered?.();
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };

  const test = async (m: ModelInfo) => {
    setErr(null); setFlash(null); setBusy(`test:${m.name}`);
    try {
      const r = await Api.testEndpoint(m.name);
      setFlash(r.health.ok
        ? `${m.name}: endpoint reachable (${r.health.detail ?? "ok"}) — server-claimed ids remain unverified declarations`
        : `${m.name}: endpoint NOT reachable (${r.health.error ?? r.health.detail ?? "unknown"})`);
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };

  const remove = async (m: ModelInfo) => {
    setErr(null); setFlash(null); setBusy(`del:${m.name}`);
    try {
      await Api.deleteModel(m.name);
      setFlash(`registry record '${m.name}' removed — model files on disk were NOT touched`);
      await load();
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(null); }
  };

  return (
    <>
      <ErrorBox error={err} />
      {flash && <div className="note mt" style={{ color: "var(--accent)" }}>{flash}</div>}

      <Card title="Register a local model">
        <div className="note mb0" style={{ marginBottom: "0.8rem" }}>
          Endpoints must be loopback (127.0.0.1 / ::1 / localhost). Remote endpoints are refused at
          registration and re-validated per request hop. A declared weights directory is hashed (SHA-256)
          into a manifest; without it, the model's artifact identity stays <b>unverified</b>.
        </div>
        <div className="grid cols2">
          <label className="field"><span>Short name (lowercase, e.g. qwen2-coder-7b-q4)</span>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-local-coder" /></label>
          <label className="field"><span>Inference endpoint (OpenAI-compatible, loopback only)</span>
            <input type="text" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="http://127.0.0.1:8080" /></label>
          <label className="field"><span>Weights directory (optional; enables artifact-identity verification)</span>
            <input type="text" value={weightsDir} onChange={(e) => setWeightsDir(e.target.value)} placeholder="/path/to/gguf-weights" /></label>
          <label className="field"><span>Server model id (optional — a CLAIM, never verified identity)</span>
            <input type="text" value={serverModelId} onChange={(e) => setServerModelId(e.target.value)} placeholder="e.g. what /v1/models reports" /></label>
          <label className="field"><span>Notes (user-declared metadata)</span>
            <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="GGUF Q4_K_M via llama.cpp b3xxx" /></label>
        </div>
        <button className="primary" disabled={!name || !endpoint || busy === "register"} onClick={register}>
          {busy === "register" ? "registering…" : "Register model"}
        </button>
      </Card>

      <Card title="Registered models">
        {models === null ? <Loading /> : models.length === 0 ? (
          <div className="note">No models registered yet. Register a loopback endpoint above, or run
            <code> modelproof model register …</code>.</div>
        ) : (
          <table>
            <thead><tr>
              <th>Name</th><th>Endpoint</th><th>Artifact identity</th><th>Server claim</th><th>Generation</th><th></th>
            </tr></thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.name}>
                  <td className="mono">{m.name}{m.declared_notes ? <div className="note">{m.declared_notes}</div> : null}</td>
                  <td className="mono">{m.endpoint}</td>
                  <td>{m.identity_verified
                    ? <Badge value="PASS">weights verified</Badge>
                    : <Badge value="NOT_RUN">UNVERIFIED</Badge>}
                    {m.weights_manifest_hash ? <div className="note mono">{m.weights_manifest_hash.slice(0, 20)}…</div> : null}</td>
                  <td className="note">{m.server_model_id ? `${m.server_model_id} (declared)` : "—"}</td>
                  <td className="mono note">{m.generation ? Object.entries(m.generation).map(([k, v]) => `${k}=${String(v)}`).join(", ") : "—"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="ghost" disabled={busy === `test:${m.name}`} onClick={() => test(m)}>
                      {busy === `test:${m.name}` ? "testing…" : "Test endpoint"}
                    </button>{" "}
                    <button className="ghost" disabled={busy === `del:${m.name}`} onClick={() => remove(m)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="note mt">
          "Remove" deletes only the registry record — never model files (mandate §5.1). Evaluation runs
          against a removed model keep their recorded history. Start an evaluation from{" "}
          <a href="#/runs">Runs</a>.
        </div>
      </Card>

      <Card title="Runtime compatibility">
        <KV rows={[
          ["Accepted backends", "OpenAI-compatible HTTP endpoints on loopback (llama.cpp server, vLLM, LM Studio, …)"],
          ["Refused", "remote hosts, DNS names that do not resolve exclusively to loopback, redirect escapes, credentials in URL"],
          ["Sandbox execution", "Docker-based, network-disabled — refused entirely when Docker is unavailable (no host fallback)"],
        ]} />
        <button className="ghost mt" onClick={() => navigate("#/runs")}>Start an evaluation →</button>
      </Card>
    </>
  );
}
