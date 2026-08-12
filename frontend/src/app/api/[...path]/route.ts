import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const backend =
  process.env.API_URL || process.env.BACKEND_URL || "http://127.0.0.1:8000";

type RouteCtx = { params: Promise<{ path: string[] }> };

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const incoming = new URL(req.url);
  const target = `${backend}/api/${path.map(encodeURIComponent).join("/")}${incoming.search}`;

  try {
    const headers = new Headers();
    const contentType = req.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    const accept = req.headers.get("accept");
    if (accept) headers.set("accept", accept);

    const init: RequestInit = {
      method: req.method,
      headers,
      cache: "no-store",
      redirect: "manual",
    };

    if (req.method !== "GET" && req.method !== "HEAD") {
      init.body = await req.arrayBuffer();
    }

    const upstream = await fetch(target, init);
    const body = await upstream.arrayBuffer();
    const outHeaders = new Headers();
    const upstreamType = upstream.headers.get("content-type");
    if (upstreamType) outHeaders.set("content-type", upstreamType);
    return new NextResponse(body, {
      status: upstream.status,
      headers: outHeaders,
    });
  } catch (err) {
    const code =
      err && typeof err === "object" && "code" in err
        ? String((err as { code?: unknown }).code)
        : "";
    const msg = err instanceof Error ? err.message : String(err);
    const unreachable =
      code === "ECONNREFUSED" ||
      code === "ECONNRESET" ||
      code === "ETIMEDOUT" ||
      /fetch failed|ECONNREFUSED|ECONNRESET|socket hang up/i.test(msg);
    const detail = unreachable
      ? "后端未启动或已崩溃。请重新运行 ./scripts/dev.sh 后刷新（同步行情已隔离，一般不会再拖垮 API）。"
      : err instanceof Error
        ? `后端代理失败：${err.message}`
        : "后端代理失败";
    return NextResponse.json({ detail }, { status: 503 });
  }
}

export async function GET(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function POST(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function PUT(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function PATCH(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function DELETE(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
