import { NextRequest, NextResponse } from "next/server";

/**
 * Same-origin proxy: /mp-api/v1/* → http://127.0.0.1:8899/api/v1/*
 *
 * The ModelProof engine binds to loopback (spec §10.4). This proxy lets the
 * platform preview surface the local dashboard without ever exposing the
 * engine beyond the machine: requests terminate at the platform gateway and
 * are forwarded locally, same as the dashboard's own same-origin contract.
 * No request bodies are transformed; no engine state is cached here.
 */

const UPSTREAM = "http://127.0.0.1:8899";

async function forward(req: NextRequest, path: string[]) {
  // The SPA calls /mp-api/v1/<endpoint>; strip the version segment so the
  // upstream URL becomes /api/v1/<endpoint> exactly once.
  const segments = path[0] === "v1" ? path.slice(1) : path;
  const target = `${UPSTREAM}/api/v1/${segments.join("/")}${req.nextUrl.search}`;
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
