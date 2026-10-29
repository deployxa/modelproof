import { useEffect, useState } from "react";
import { Api, type Health } from "./api";
import { useHashRoute } from "./ui";
import Home from "./screens/Home";
import Models from "./screens/Models";
import Suites from "./screens/Suites";
import Runs from "./screens/Runs";
import RunDetail from "./screens/RunDetail";
import Compare from "./screens/Compare";
import Settings from "./screens/Settings";

type Route =
  | { screen: "home" } | { screen: "models" } | { screen: "suites" }
  | { screen: "runs" } | { screen: "run"; id: string; tab?: "results" | "evidence" }
  | { screen: "compare" } | { screen: "settings" };

function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [head, id, tab] = parts;
  if (head === "models") return { screen: "models" };
  if (head === "suites") return { screen: "suites" };
  if (head === "compare") return { screen: "compare" };
  if (head === "settings") return { screen: "settings" };
  if (head === "runs" && id) return { screen: "run", id, tab: tab === "results" ? "results" : tab === "evidence" ? "evidence" : undefined };
  if (head === "runs") return { screen: "runs" };
  return { screen: "home" };
}

export default function App() {
  const route = parseRoute(useHashRoute());
  const [health, setHealth] = useState<Health | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const refreshHealth = () =>
    Api.health().then((h) => { setHealth(h); setApiError(null); })
      .catch((e) => setApiError(String(e.message || e)));

  useEffect(() => {
    refreshHealth();
    const t = setInterval(refreshHealth, 5000);
    return () => clearInterval(t);
  }, []);

  const navItems: [Route["screen"], string, string][] = [
    ["home", "Home", "#/home"],
    ["models", "Models", "#/models"],
    ["suites", "Suites", "#/suites"],
    ["runs", "Runs", "#/runs"],
    ["compare", "Compare", "#/compare"],
    ["settings", "Settings", "#/settings"],
  ];

  return (
    <>
      <header className="topbar">
        <div className="logo">Model<span>Proof</span></div>
        <div className="tag">local-only evaluation lab · evidence-first · v{health?.engine_version ?? "0.2"}</div>
        <nav className="tabs" aria-label="screens">
          {navItems.map(([screen, label, href]) => (
            <a key={screen} href={href} className={`tab-link ${route.screen === screen || (route.screen === "run" && screen === "runs") ? "active" : ""}`}>{label}</a>
          ))}
        </nav>
      </header>

      <main>
        {apiError && (
          <div className="error-panel">
            Cannot reach the ModelProof engine at <code>{"/api/v1"}</code>: {apiError}
            <div className="note mt">Start it with <code>modelproof serve</code> (binds 127.0.0.1 by default).</div>
          </div>
        )}
        {route.screen === "home" && <Home />}
        {route.screen === "models" && <Models />}
        {route.screen === "suites" && <Suites />}
        {route.screen === "runs" && <Runs />}
        {route.screen === "run" && <RunDetail key={route.id} runId={route.id} initialTab={route.tab} />}
        {route.screen === "compare" && <Compare />}
        {route.screen === "settings" && <Settings />}
      </main>

      <footer>
        The dashboard distinguishes <span className="badge ok">verified facts</span> from{" "}
        <span className="badge info">user-declared metadata</span> and run integrity from model performance.
        Inference stays loopback; generated code only ever runs in the sandbox — never on the host.
      </footer>
    </>
  );
}
