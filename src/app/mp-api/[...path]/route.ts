import { NextRequest, NextResponse } from "next/server";
import { spawn } from "node:child_process";
import net from "node:net";

/**
 * Same-origin proxy: /mp-api/* → http://127.0.0.1:8899/api/v1/*
 *
 * The ModelProof engine binds to loopback (spec §10.4). This proxy lets the
 * platform preview surface the local dashboard without ever exposing the
 * engine beyond the machine: requests terminate at the platform gateway and
 * are forwarded locally, same as the dashboard's own same-origin contract.
 * No request bodies are transformed; no engine state is cached here.
 *
 * Engine supervision: the engine is a local-only background service. If it is
 * not listening, this proxy spawns it (fixed command, fixed port — no user
 * input involved) and waits for /api/v1/health before retrying the forward.
 * The engine refuses to bind non-loopback hosts by its own policy.
 */

const UPSTREAM_HOST = "127.0.0.1";
const UPSTREAM_PORT = 8899;
const UPSTREAM = `http://${UPSTREAM_HOST}:${UPSTREAM_PORT}`;
const ENGINE_CWD = "/home/z/my-project/modelproof";
const ENGINE_BIN = "/home/z/.venv/bin/modelproof";

let starting: Promise<boolean> | null = null;

function portListening(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = net.connect({ port, host });
    sock.setTimeout(700);
    sock.once("connect", () => { sock.destroy(); resolve(true); });
    sock.once("timeout", () => { sock.destroy(); resolve(false); });
    sock.once("error", () => resolve(false));
  });
}

async function ensureEngine(): Promise<boolean> {
  if (await portListening(UPSTREAM_PORT, UPSTREAM_HOST)) return true;
  if (!starting) {
    starting = (async () => {
      try {
        // logs go to a file so engine tracebacks remain inspectable
        const child = spawn("/bin/bash", [
          "-c",
          `exec ${ENGINE_BIN} serve --port ${UPSTREAM_PORT} >> /tmp/modelproof-engine.log 2>&1`,
        ], {
          cwd: ENGINE_CWD,
          detached: true,
          stdio: "ignore",
        });
        child.unref();
      } catch {
        /* fall through to polling; the proxy will report 502 if it never comes up */
      }
      for (let i = 0; i < 24; i++) {
        await new Promise((r) => setTimeout(r, 250));
        if (await portListening(UPSTREAM_PORT, UPSTREAM_HOST)) return true;
      }
      return false;
    })().finally(() => { starting = null; });
  }
  return starting;
}

async function forward(req: NextRequest, path: string[]) {
  // The SPA calls /mp-api/v1/<endpoint>; strip the version segment so the
  // upstream URL becomes /api/v1/<endpoint> exactly once.
  const segments = path[0] === "v1" ? path.slice(1) : path;
  const target = `${UPSTREAM}/api/v1/${segments.join("/")}${req.nextUrl.search}`;

  if (!(await ensureEngine())) {
    return NextResponse.json(
      { detail: "ModelProof engine could not be started on 127.0.0.1:8899 — run `modelproof serve` manually." },
      { status: 502 },
    );
  }

  const headers = new Headers();
  headers.set("accept", req.headers.get("accept") ?? "application/json");
  if (req.headers.has("content-type")) {
    headers.set("content-type", req.headers.get("content-type") as string);
  }
  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
      cache: "no-store",
    });
    const respHeaders = new Headers();
    respHeaders.set("content-type", upstream.headers.get("content-type") ?? "application/json");
    return new NextResponse(upstream.body, { status: upstream.status, headers: respHeaders });
  } catch (e) {
    return NextResponse.json(
      {
        detail:
          "ModelProof engine unreachable on 127.0.0.1:8899 — start it with `modelproof serve` " +
          `(${(e as Error).message})`,
      },
      { status: 502 },
    );
  }
}

type Ctx = { params: Promise<{ path?: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { path = [] } = await ctx.params;
  return forward(req, path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  const { path = [] } = await ctx.params;
  return forward(req, path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  const { path = [] } = await ctx.params;
  return forward(req, path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  const { path = [] } = await ctx.params;
  return forward(req, path);
}
