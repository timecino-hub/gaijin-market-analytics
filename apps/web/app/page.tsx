import Link from "next/link";

export default function Home() {
  return (
    <main className="home-page">
      <section className="intro" aria-labelledby="page-title">
        <p className="eyebrow">Imported data browser</p>
        <h1 id="page-title">Gaijin Market Analytics</h1>
        <p>
          浏览 CSV、JSON、手动或明确授权来源导入的市场数据，提供描述性统计和后续分析的基础。
        </p>
        <div className="hero-actions">
          <Link className="primary-link" href="/imports">
            导入 CSV
          </Link>
          <Link className="primary-link" href="/items">
            浏览商品数据
          </Link>
        </div>
      </section>
      <section className="notice" aria-labelledby="notice-title">
        <h2 id="notice-title">合规边界</h2>
        <p>
          本项目只提供数据分析参考，不构成收益保证；不会访问 Gaijin Market、不会自动登录、
          不会执行买卖、撤单、支付或账户控制。
        </p>
        <p>收益与风险分析功能将在后续阶段加入。</p>
      </section>
      <section className="status" aria-label="项目状态">
        <div>
          <span>Web</span>
          <strong>Ready on port 3000</strong>
        </div>
        <div>
          <span>API</span>
          <strong>Health check on port 8000</strong>
        </div>
        <div>
          <span>Database</span>
          <strong>PostgreSQL on port 5432</strong>
        </div>
      </section>
    </main>
  );
}
