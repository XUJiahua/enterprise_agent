import { NextRequest, NextResponse } from "next/server";

const identities = new Set(["alice", "bob", "carol", "dave", "erin"]);
export const dynamic = "force-dynamic";
async function proxy(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const actor = req.headers.get("x-demo-identity") ?? "";
  if (!identities.has(actor))
    return NextResponse.json(
      { error: "请选择有效的体验身份" },
      { status: 401 },
    );
  const { path } = await context.params;
  const valid =
    path[0] === "purchases" &&
    (path.length === 1 ||
      (path.length === 2 && /^[a-zA-Z0-9_-]{1,100}$/.test(path[1])) ||
      (path.length === 3 &&
        /^[a-zA-Z0-9_-]{1,100}$/.test(path[1]) &&
        path[2] === "approve"));
  if (!valid)
    return NextResponse.json({ error: "接口不存在" }, { status: 404 });
  if (!["GET", "POST", "PATCH"].includes(req.method))
    return NextResponse.json({ error: "不支持该操作" }, { status: 405 });
  // Backend origin is fixed. Browser input cannot choose a URL or forward auth/tenant/role.
  const url = "http://127.0.0.1:8080/" + path.map(encodeURIComponent).join("/");
  const headers = {
    "Content-Type": "application/json",
    Authorization: "Bearer demo_" + actor,
  };
  try {
    const body = req.method === "GET" ? undefined : await req.text();
    if (body && new TextEncoder().encode(body).length > 65536)
      return NextResponse.json({ error: "请求内容过大" }, { status: 413 });
    const upstream = await fetch(url, {
      method: req.method,
      headers,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10000),
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { error: "采购服务暂时不可用，请稍后重试" },
      { status: 503 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH };
