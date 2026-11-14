import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Procure Lab · 采购审批",
  description: "三条技术路径共用的采购审批工作台",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
