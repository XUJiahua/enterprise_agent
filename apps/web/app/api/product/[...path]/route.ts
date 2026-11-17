import { NextRequest, NextResponse } from "next/server";

// Proxy for the v0.1 product API (cmd/product). Only the listed paths reach
// the backend; identity comes from the demo selector, never from the client
// as a tenant/role, and the backend origin is fixed to loopback.
const identities = new Set(["alice", "bob", "carol", "dave", "erin"]);
const reads = new Set(["purchases", "activity"]);
// Current operation versions; retired versions (procurement.close@1) are not proxied.
const operations: Record<string, string> = {
  "procurement.approve": "1",
  "procurement.receive": "1",
  "procurement.close": "2",
};
export const dynamic = "force-dynamic";

async function proxy(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const actor = req.headers.get("x-demo-identity") ?? "";
  if (!identities.has(actor))
    return NextResponse.json({ message: "请选择有效的体验身份" }, { status: 401 });
  const { path } = await context.params;
  const read = req.method === "GET" && path.length === 1 && reads.has(path[0]);
  const write =
    req.method === "POST" &&
    path.length === 3 &&
    path[0] === "operations" &&
    Object.hasOwn(operations, path[1]) &&
    path[2] === operations[path[1]];
  if (!read && !write)
    return NextResponse.json({ message: "接口不存在" }, { status: 404 });
  const key = req.headers.get("idempotency-key") ?? "";
  if (write && !/^[A-Za-z0-9._:-]{1,128}$/.test(key))
    return NextResponse.json({ message: "缺少幂等键" }, { status: 400 });
  const port = Number(process.env.PRODUCT_HTTP_PORT ?? "8090");
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error("invalid PRODUCT_HTTP_PORT");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: "Bearer demo_" + actor,
  };
  if (write) headers["Idempotency-Key"] = key;
  try {
    const body = write ? await req.text() : undefined;
    if (body && new TextEncoder().encode(body).length > 65536)
      return NextResponse.json({ message: "请求内容过大" }, { status: 413 });
    const upstream = await fetch(
      `http://127.0.0.1:${port}/v1/` + path.map(encodeURIComponent).join("/"),
      {
        method: req.method,
        headers,
        body,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10000),
      },
    );
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { message: "采购服务暂时不可用，请稍后重试" },
      { status: 503 },
    );
  }
}
export { proxy as GET, proxy as POST };
