import Link from "next/link";

export function MarketHeader({ active }: { active?: "home" | "catalog" | "opportunities" }) {
  return (
    <header className="deco-header">
      <Link className="deco-brand" href="/" aria-label="Gaijin Market Analytics 首页">
        <span className="deco-monogram" aria-hidden="true">
          <i>G</i><i>M</i><i>A</i>
        </span>
        <span>Gaijin Market Analytics</span>
      </Link>
      <nav aria-label="主导航">
        <Link className={active === "home" ? "active" : undefined} href="/">
          市场概览
        </Link>
        <Link className={active === "catalog" ? "active" : undefined} href="/items">
          载具目录
        </Link>
        <Link className={active === "opportunities" ? "active" : undefined} href="/opportunities">
          潜力分析
        </Link>
      </nav>
      <span className="deco-readonly">只读预览</span>
    </header>
  );
}
