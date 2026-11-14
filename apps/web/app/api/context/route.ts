import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
export function GET() {
  const route = process.env.POC_ROUTE;
  if (!route || !["baseline", "guarded", "zenstack"].includes(route))
    return NextResponse.json({ error: "POC_ROUTE 未配置" }, { status: 503 });
  return NextResponse.json(
    { route },
    { headers: { "Cache-Control": "no-store" } },
  );
}
