import Link from "next/link";

export default function NotFound() {
  return <main className="art-shell"><section className="deco-state"><span aria-hidden="true">404</span><div><h1>页面不存在</h1><p>请求的市场页面不存在或尚未开放。</p><Link href="/">返回市场概览 →</Link></div></section></main>;
}
