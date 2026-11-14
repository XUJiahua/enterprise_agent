"use client";
import { useEffect, useRef, useState } from "react";

type Purchase = {
  id: string;
  tenantId: string;
  createdBy: string;
  title: string;
  amount: number;
  status: "submitted" | "approved";
};
const identities = {
  alice: { label: "Alice · 申请人", tenant: "tenant-a", role: "requester" },
  bob: { label: "Bob · 审批人", tenant: "tenant-a", role: "approver" },
  carol: { label: "Carol · 审批人", tenant: "tenant-a", role: "approver" },
  dave: { label: "Dave · 申请人", tenant: "tenant-b", role: "requester" },
  erin: { label: "Erin · 审批人", tenant: "tenant-b", role: "approver" },
} as const;
type Actor = keyof typeof identities;
const routes: Record<string, string> = {
  baseline: "普通 Go",
  zenstack: "ZenStack V3",
  guarded: "Go + Policy / RLS",
};
const money = (n: number) =>
  new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(n);
export default function Home() {
  const [actor, setActor] = useState<Actor>("alice");
  const [route, setRoute] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [rows, setRows] = useState<Purchase[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [title, setTitle] = useState("");
  const [amount, setAmount] = useState("");
  const generation = useRef(0);
  const current = identities[actor];
  async function api(
    path: string,
    method = "GET",
    data?: unknown,
    signal?: AbortSignal,
  ) {
    const response = await fetch("/api/backend/" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-Demo-Identity": actor },
      body: data === undefined ? undefined : JSON.stringify(data),
      signal,
      cache: "no-store",
    });
    if (!response.ok)
      throw new Error(
        response.status === 503
          ? "采购服务暂时不可用，请稍后重试。"
          : `操作未完成（${response.status}），请确认当前身份和采购单状态。`,
      );
    return response.json();
  }
  useEffect(() => {
    fetch("/api/context", { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw Error("无法读取运行环境");
        return r.json();
      })
      .then((v) => setRoute(v.route))
      .catch(() => setError("无法读取运行环境，请检查服务配置。"));
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const version = ++generation.current;
    setRows([]);
    setLoading(true);
    setError("");
    setNotice("");
    api("purchases", "GET", undefined, controller.signal)
      .then((data) => {
        if (version === generation.current) setRows(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted && version === generation.current)
          setError(e.message);
      })
      .finally(() => {
        if (version === generation.current) setLoading(false);
      });
    return () => controller.abort();
    // Identity changes invalidate pending responses before rendering the next tenant.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actor, refresh]);
  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("purchases", "POST", {
        id: "ui-" + crypto.randomUUID(),
        title: title.trim(),
        amount: Number(amount),
      });
      setRows(await api("purchases"));
      setTitle("");
      setAmount("");
      setNotice("采购单已提交，等待审批。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function approve(id: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("purchases/" + encodeURIComponent(id) + "/approve", "POST");
      setRows(await api("purchases"));
      setNotice("审批已完成。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function switchActor(next: Actor) {
    generation.current++;
    setRows([]);
    setLoading(true);
    setError("");
    setNotice("");
    setActor(next);
  }
  const pending = rows.filter((r) => r.status === "submitted").length;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#requests">
          <span className="brand-mark">P</span>
          <span>
            Procure<span className="brand-sub">APPLICATION LAB</span>
          </span>
        </a>
        <span className="nav-label">工作空间</span>
        <a className="nav-item active" href="#requests">
          <span>▦</span>采购申请<span className="nav-count">{rows.length}</span>
        </a>
        <div className="sidebar-note">
          <span className="small-dot" />
          可验证的业务规则<p>每一笔采购，都有清晰的权限与状态。</p>
        </div>
      </aside>
      <main id="requests">
        <header className="topbar">
          <span>
            工作空间 <span className="slash">/</span> 采购申请
          </span>
          <span className="environment">
            <span className="small-dot" />
            {routes[route] ?? "连接中"}
          </span>
        </header>
        <section className="content">
          <div className="heading">
            <div>
              <p className="eyebrow">PURCHASE WORKSPACE</p>
              <h1>采购审批工作台</h1>
              <p className="intro">让申请与审批，在同一个工作空间流转。</p>
            </div>
            <span className="demo-label">POC 体验环境</span>
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
            <p>切换身份可体验不同租户和审批权限。</p>
          </div>
          <div className="metrics">
            <article>
              <span>采购单总数</span>
              <strong>
                {loading ? "—" : rows.length}
                <small>笔</small>
              </strong>
              <p>当前租户的全部采购申请</p>
            </article>
            <article>
              <span>待审批</span>
              <strong className="accent">
                {loading ? "—" : pending}
                <small>笔</small>
              </strong>
              <p>等待审批人处理</p>
            </article>
            <article>
              <span>申请总金额</span>
              <strong>
                {loading
                  ? "—"
                  : money(rows.reduce((sum, row) => sum + row.amount, 0))}
              </strong>
              <p>演示金额 · CNY</p>
            </article>
          </div>
          {notice && (
            <div className="notice" role="status">
              ✓ {notice}
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
              <button
                onClick={() => setRefresh((value) => value + 1)}
                disabled={busy}
              >
                重试
              </button>
            </div>
          )}
          <div className="workspace-grid">
            <section className="requests panel">
              <div className="panel-heading">
                <div>
                  <h2>全部采购单</h2>
                  <p>仅显示当前租户可访问的记录</p>
                </div>
                <span className="subtle-tag">{rows.length} 条记录</span>
              </div>
              {loading ? (
                <p className="empty" role="status">
                  正在载入采购单…
                </p>
              ) : rows.length === 0 ? (
                <p className="empty">暂无采购单，创建第一笔采购申请。</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>采购项目</th>
                        <th>申请人</th>
                        <th>金额</th>
                        <th>状态</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr key={row.id} data-testid={"purchase-" + row.id}>
                          <td>
                            <strong>{row.title}</strong>
                            <small>{row.id}</small>
                          </td>
                          <td>
                            <span className="avatar">
                              {row.createdBy[0].toUpperCase()}
                            </span>
                            {row.createdBy}
                          </td>
                          <td className="amount">{money(row.amount)}</td>
                          <td>
                            <span className={"status " + row.status}>
                              {row.status === "submitted" ? "待审批" : "已批准"}
                            </span>
                          </td>
                          <td>
                            {row.status === "approved" ? (
                              <span className="muted">已完成</span>
                            ) : current.role === "approver" &&
                              row.createdBy !== actor ? (
                              <button
                                className="approve"
                                disabled={busy}
                                onClick={() => approve(row.id)}
                              >
                                批准
                              </button>
                            ) : (
                              <span className="muted">
                                {row.createdBy === actor
                                  ? "本人提交"
                                  : "仅审批人可操作"}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="create panel">
              <div className="panel-heading">
                <div>
                  <h2>新建采购单</h2>
                  <p>提交一笔新的采购申请</p>
                </div>
                <span className="form-icon">＋</span>
              </div>
              <form onSubmit={create}>
                <label htmlFor="title">采购项目</label>
                <input
                  id="title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="例如：研发团队笔记本电脑"
                  maxLength={200}
                  required
                  disabled={busy || current.role !== "requester"}
                />
                <label htmlFor="amount">
                  申请金额 <span>CNY</span>
                </label>
                <input
                  id="amount"
                  type="number"
                  min="1"
                  max="2147483647"
                  step="1"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="输入正整数金额"
                  required
                  disabled={busy || current.role !== "requester"}
                />
                <div className="form-info">
                  租户和申请人由当前身份自动绑定。
                  <br />
                  提交后需要其他审批人批准。
                </div>
                <button
                  className="primary"
                  type="submit"
                  disabled={busy || loading || current.role !== "requester"}
                >
                  {busy ? "处理中…" : "提交采购单"}
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 20 20"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path
                      d="M5 15 15 5M5 5h10v10"
                      stroke="currentColor"
                      strokeWidth="1.5"
                    />
                  </svg>
                </button>
                {current.role !== "requester" && (
                  <p className="role-hint">
                    当前为审批身份，请切换至申请人创建采购单。
                  </p>
                )}
              </form>
            </section>
          </div>
          <footer>
            Procure Lab <span>·</span>{" "}
            三条技术路径，共用一套业务体验。身份切换仅用于演示。
          </footer>
        </section>
      </main>
    </div>
  );
}
