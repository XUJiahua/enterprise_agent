"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// v0.1 product page: receipts, bulk close and notifications on cmd/product.
type Purchase = {
  id: string;
  title: string;
  amount: number;
  status: "submitted" | "approved" | "closed";
  createdBy: string;
  received: number;
};
type AuditEntry = {
  operation: string;
  version: number;
  actor: string;
  target: string;
  change: Record<string, unknown>;
  at: string;
};
type Notification = { purchaseId: string; kind: string; at: string };
const identities = {
  alice: { label: "Alice · 申请人", tenant: "tenant-a", role: "requester" },
  bob: { label: "Bob · 审批人", tenant: "tenant-a", role: "approver" },
  carol: { label: "Carol · 审批人", tenant: "tenant-a", role: "approver" },
  dave: { label: "Dave · 申请人", tenant: "tenant-b", role: "requester" },
  erin: { label: "Erin · 审批人", tenant: "tenant-b", role: "approver" },
} as const;
type Actor = keyof typeof identities;
const statusLabel = { submitted: "待审批", approved: "已批准", closed: "已关闭" };
const operationLabel: Record<string, string> = {
  "procurement.approve": "批准",
  "procurement.receive": "登记验收",
  "procurement.close": "关闭",
};
const money = (n: number) =>
  new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(n);

export default function Procurement() {
  const [actor, setActor] = useState<Actor>("bob");
  const [rows, setRows] = useState<Purchase[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [notes, setNotes] = useState<Notification[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [receipt, setReceipt] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const current = identities[actor];

  const api = useCallback(
    async (path: string, body?: unknown) => {
      const response = await fetch("/api/product/" + path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Demo-Identity": actor,
          ...(body === undefined ? {} : { "Idempotency-Key": crypto.randomUUID() }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok)
        throw Object.assign(new Error(data.message ?? "请求失败"), { status: response.status });
      return data;
    },
    [actor],
  );

  const load = useCallback(async () => {
    const version = generation.current;
    const [p, a] = await Promise.all([api("purchases"), api("activity")]);
    if (version !== generation.current) return; // identity changed meanwhile
    setRows(p.purchases);
    setAudit(a.audit);
    setNotes(a.notifications);
  }, [api]);

  useEffect(() => {
    load().catch((e) => setError(e.message));
    // Notifications arrive asynchronously from the outbox consumer.
    const timer = setInterval(() => load().catch(() => {}), 1500);
    return () => clearInterval(timer);
  }, [load]);

  function switchActor(next: Actor) {
    generation.current++;
    setRows([]);
    setAudit([]);
    setNotes([]);
    setSelected([]);
    setError("");
    setNotice("");
    setActor(next);
  }

  async function act(run: () => Promise<string>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await run());
      await load();
    } catch (e) {
      const status = (e as { status?: number }).status;
      setError(
        status === 403
          ? "操作未执行：至少一张采购单不满足条件（状态、权限或验收金额），所有采购单保持不变。"
          : (e as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }

  const closeSelected = () =>
    act(async () => {
      const r = await api("operations/procurement.close/2", { ids: selected, reason: reason.trim() });
      setSelected([]);
      setReason("");
      return `已关闭 ${r.closedIds.length} 张采购单：${r.closedIds.join("、")}`;
    });
  const receive = (id: string) =>
    act(async () => {
      const amount = Number(receipt[id]);
      await api("operations/procurement.receive/1", { purchaseId: id, amount });
      setReceipt((v) => ({ ...v, [id]: "" }));
      return `已登记 ${id} 的验收金额 ${money(amount)}`;
    });
  const toggle = (id: string) =>
    setSelected((v) => (v.includes(id) ? v.filter((x) => x !== id) : [...v, id]));

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#purchases">
          <span className="brand-mark">P</span>
          <span>
            Procure<span className="brand-sub">v0.1 KERNEL</span>
          </span>
        </a>
        <span className="nav-label">工作空间</span>
        <a className="nav-item active" href="#purchases">
          <span>▦</span>验收与关闭<span className="nav-count">{rows.length}</span>
        </a>
      </aside>
      <main id="purchases">
        <section className="content">
          <div className="heading">
            <div>
              <p className="eyebrow">PROCUREMENT</p>
              <h1>验收与批量关闭</h1>
              <p className="intro">验收金额覆盖采购金额后，审批人可整批关闭采购单。</p>
            </div>
          </div>
          <div className="identity-panel">
            <div>
              <span className="identity-icon">{actor[0].toUpperCase()}</span>
              <div>
                <label htmlFor="identity">当前体验身份</label>
                <select
                  id="identity"
                  value={actor}
                  disabled={busy}
                  onChange={(e) => switchActor(e.target.value as Actor)}
                >
                  {Object.entries(identities).map(([key, value]) => (
                    <option key={key} value={key}>
                      {value.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <span className="tenant">
              当前租户 <strong data-testid="tenant">{current.tenant}</strong>
            </span>
          </div>
          {notice && (
            <div className="notice" role="status">
              ✓ {notice}
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <section className="requests panel">
            <div className="panel-heading">
              <div>
                <h2>采购单</h2>
                <p>仅显示当前租户可访问的记录</p>
              </div>
              {current.role === "approver" && (
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    aria-label="关闭原因"
                    placeholder="关闭原因（必填）"
                    maxLength={200}
                    value={reason}
                    disabled={busy}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <button
                    className="primary"
                    style={{ width: "auto", marginTop: 0 }}
                    disabled={busy || selected.length === 0 || reason.trim() === ""}
                    onClick={closeSelected}
                  >
                    批量关闭所选（{selected.length}）
                  </button>
                </span>
              )}
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>选择</th>
                    <th>采购项目</th>
                    <th>申请人</th>
                    <th>金额</th>
                    <th>已验收</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} data-testid={"purchase-" + row.id}>
                      <td>
                        {current.role === "approver" && row.status === "approved" && (
                          <input
                            type="checkbox"
                            aria-label={"选择 " + row.id}
                            checked={selected.includes(row.id)}
                            disabled={busy}
                            onChange={() => toggle(row.id)}
                          />
                        )}
                      </td>
                      <td>
                        <strong>{row.title}</strong>
                        <small>{row.id}</small>
                      </td>
                      <td>{row.createdBy}</td>
                      <td className="amount">{money(row.amount)}</td>
                      <td className="amount" data-testid={"received-" + row.id}>
                        {money(row.received)}
                      </td>
                      <td>
                        <span className={"status " + row.status}>{statusLabel[row.status]}</span>
                      </td>
                      <td>
                        {current.role === "requester" && row.status === "approved" ? (
                          <span>
                            <input
                              type="number"
                              min="1"
                              step="1"
                              aria-label={"验收金额 " + row.id}
                              value={receipt[row.id] ?? ""}
                              disabled={busy}
                              onChange={(e) => setReceipt((v) => ({ ...v, [row.id]: e.target.value }))}
                            />
                            <button
                              className="approve"
                              disabled={busy || !(Number(receipt[row.id]) > 0)}
                              onClick={() => receive(row.id)}
                            >
                              登记验收
                            </button>
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <div className="workspace-grid">
            <section className="requests panel" aria-label="我的通知">
              <div className="panel-heading">
                <div>
                  <h2>我的通知</h2>
                  <p>由 outbox 消费者异步生成</p>
                </div>
              </div>
              {notes.length === 0 ? (
                <p className="empty">暂无通知</p>
              ) : (
                <ul data-testid="notifications">
                  {notes.map((n) => (
                    <li key={n.purchaseId + n.at}>采购单 {n.purchaseId} 已关闭</li>
                  ))}
                </ul>
              )}
            </section>
            <section className="create panel" aria-label="审计记录">
              <div className="panel-heading">
                <div>
                  <h2>审计记录</h2>
                  <p>最近 50 条，当前租户</p>
                </div>
              </div>
              <ul data-testid="audit">
                {audit.map((a, i) => (
                  <li key={i}>
                    {a.actor} {operationLabel[a.operation] ?? a.operation} {a.target}
                    {typeof a.change.reason === "string" && a.change.reason !== "" && <> · 原因：{a.change.reason}</>}
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}
