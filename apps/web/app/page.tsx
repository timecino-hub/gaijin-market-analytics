import Link from "next/link";
import { getItems, toDisplayError } from "../lib/api-client";
import { formatDateTime } from "../lib/formatters";
import type { ItemSummary } from "../lib/types";
import { MarketHeader } from "./market-header";

export const dynamic = "force-dynamic";

export default async function Home() {
  const result = await loadRecentItems();
  const featured = result.ok ? result.items[0] : undefined;

  return (
    <main className="art-shell">
      <MarketHeader active="home" />
      <section className="deco-intro" aria-labelledby="home-title">
        <div>
          <p className="deco-kicker">VEHICLE MARKET OUTLOOK</p>
          <h1 id="home-title">载具市场概览</h1>
          <p>浏览经人工确认的数据，以统一价格合同查看当前订单簿，并按真实历史观测筛选市场机会。</p>
        </div>
        <form className="deco-search" action="/items">
          <label htmlFor="home-search">搜索载具</label>
          <div>
            <input id="home-search" name="search" type="search" placeholder="名称或 external key" />
            <button type="submit">搜索目录</button>
          </div>
        </form>
      </section>

      {!result.ok ? (
        <section className="deco-feature deco-feature-error" aria-live="polite">
          <div className="deco-feature-index">!</div>
          <div><p className="deco-kicker">SERVICE STATUS</p><h2>市场数据暂不可用</h2><p>{result.message}</p></div>
        </section>
      ) : featured ? <FeaturedItem item={featured} /> : (
        <section className="deco-feature deco-feature-empty">
          <div className="deco-stage" aria-hidden="true">
            <span className="deco-block block-one" /><span className="deco-block block-two" /><strong>GMA</strong>
          </div>
          <div className="deco-feature-copy">
            <p className="deco-kicker">CONFIRMED DATA ONLY</p><h2>等待首批审核数据</h2>
            <p>生产目录目前为空。只有通过受控采集流程确认的商品与订单簿会出现在这里。</p>
            <Link className="deco-outline-link" href="/items">查看空目录 <span>→</span></Link>
          </div>
        </section>
      )}

      <section className="deco-status" aria-label="服务与数据状态">
        <Status label="API" value={result.ok ? "在线" : "不可用"} emphasis={result.ok} />
        <Status label="数据边界" value="仅审核导入" />
        <Status label="价格合同" value="GJN current-book v1" />
        <Status label="自动采集" value="禁用" />
      </section>

      <section className="deco-list-section" aria-labelledby="recent-title">
        <div className="deco-section-heading">
          <div><p className="deco-kicker">CATALOG</p><h2 id="recent-title">最近可浏览载具</h2></div>
          <Link href="/items">完整目录 <span>→</span></Link>
        </div>
        {result.ok && result.items.length > 0 ? (
          <div className="deco-catalog-list">
            <div className="deco-list-head" aria-hidden="true"><span>载具</span><span>当前买价</span><span>当前卖价</span><span>数据状态</span><span /></div>
            {result.items.map((item) => <CatalogRow item={item} key={item.id} />)}
          </div>
        ) : (
          <div className="deco-empty-inline"><strong>{result.ok ? "暂无已审核载具" : "无法读取目录"}</strong><p>{result.ok ? "导入完成后，载具会按真实目录数据出现在这里。" : result.message}</p></div>
        )}
      </section>

      <section className="deco-deferred" aria-labelledby="analytics-title">
        <div><p className="deco-kicker">ANALYTICS</p><h2 id="analytics-title">潜力分析已开放</h2></div>
        <div><p>当前不生成示例评分或收益预测；真实市场观测达到评分门槛后才会进入排名。</p><Link href="/opportunities">查看分析口径 <span>→</span></Link></div>
      </section>
    </main>
  );
}

function FeaturedItem({ item }: { item: ItemSummary }) {
  const book = item.current_order_book;
  return (
    <section className="deco-feature deco-feature-item" aria-labelledby="featured-title">
      <div className="deco-stage" aria-hidden="true"><span className="deco-block block-one" /><span className="deco-block block-two" /><strong>{item.name.slice(0, 2).toUpperCase()}</strong></div>
      <div className="deco-feature-copy">
        <p className="deco-kicker">LATEST CATALOG ENTRY</p><h2 id="featured-title">{item.name}</h2><p>{item.external_key}</p>
        <div className="deco-feature-prices"><Price label="当前买价" value={book?.best_buy.canonical_display_text} /><Price label="当前卖价" value={book?.best_sell.canonical_display_text} /></div>
        <Link className="deco-primary-link" href={`/items/${item.id}`}>查看订单簿 <span>→</span></Link>
      </div>
    </section>
  );
}

function CatalogRow({ item }: { item: ItemSummary }) {
  const book = item.current_order_book;
  return (
    <Link className="deco-catalog-row" href={`/items/${item.id}`}>
      <div><strong>{item.name}</strong><small>{item.category} · {item.external_key}</small></div>
      <span className="deco-number">{book ? `${book.best_buy.canonical_display_text} GJN` : "—"}</span>
      <span className="deco-number">{book ? `${book.best_sell.canonical_display_text} GJN` : "—"}</span>
      <span>{book ? `${book.freshness === "fresh" ? "当前" : "已过期"} · ${formatDateTime(book.captured_at)}` : statusLabel(item)}</span><b>→</b>
    </Link>
  );
}

function Price({ label, value }: { label: string; value?: string }) {
  return <div><span>{label}</span><strong>{value ?? "数据不足"}</strong>{value ? <small>GJN</small> : null}</div>;
}

function Status({ label, value, emphasis = false }: { label: string; value: string; emphasis?: boolean }) {
  return <div><span>{label}</span><strong className={emphasis ? "is-online" : undefined}>{value}</strong></div>;
}

function statusLabel(item: ItemSummary): string {
  return item.current_order_book_status === "contract_error" ? "价格合同不适用" : "暂无订单簿";
}

async function loadRecentItems() {
  try {
    const data = await getItems({ page: "1", page_size: "6", sort: "updated_at", order: "desc" });
    return { ok: true as const, items: data.items };
  } catch (error) {
    return { ok: false as const, items: [], message: toDisplayError(error).message };
  }
}
