import Link from "next/link";
import { getCurrentOrderBook, getItem, toDisplayError } from "../../../lib/api-client";
import { formatDateTime } from "../../../lib/formatters";
import type { ApiError, CurrentOrderBook, CurrentOrderBookLevel, ItemDetail } from "../../../lib/types";
import { MarketHeader } from "../../market-header";

export const dynamic = "force-dynamic";

type ItemDetailPageProps = { params: Promise<{ itemId: string }> };

export default async function ItemDetailPage({ params }: ItemDetailPageProps) {
  const { itemId } = await params;
  const result = await loadItem(itemId);
  if ("error" in result) return <ItemError error={result.error} />;
  const { item, orderBook } = result;

  return (
    <main className="art-shell">
      <MarketHeader active="catalog" />
      <div className="deco-breadcrumb"><Link href="/items">载具目录</Link><span>/</span><span>{item.name}</span></div>
      <section className="deco-detail-masthead">
        <div className="deco-detail-geometry" aria-hidden="true"><i /><i /><strong>{item.name.slice(0, 2).toUpperCase()}</strong></div>
        <div className="deco-detail-title">
          <p className="deco-kicker">{item.category || "UNCATEGORIZED"}</p>
          <h1>{item.name}</h1>
          <p>{item.external_key}</p>
          <span className={`deco-freshness ${orderBook?.freshness === "stale" ? "stale" : ""}`}>
            {orderBook ? (orderBook.freshness === "fresh" ? "当前订单簿" : "订单簿已过期") : "暂无订单簿"}
          </span>
        </div>
        {orderBook ? <DetailPrices orderBook={orderBook} /> : <div className="deco-detail-no-price"><strong>数据不足</strong><span>尚无已批准的 current-book 观测</span></div>}
      </section>

      {!orderBook ? (
        <section className="deco-empty-large"><span aria-hidden="true">00</span><div><strong>暂无已确认订单簿</strong><p>该载具存在于目录中，但当前没有通过价格合同校验的订单簿。</p></div></section>
      ) : (
        <>
          <section className="deco-orderbook" aria-labelledby="orderbook-title">
            <div className="deco-section-heading">
              <div><p className="deco-kicker">CURRENT DEPTH</p><h2 id="orderbook-title">当前订单簿</h2></div>
              <span>{orderBook.schema_version}</span>
            </div>
            <div className="deco-orderbook-grid">
              <OrderBookSide title="买方报价" levels={orderBook.buy_levels} side="BUY" />
              <OrderBookSide title="卖方报价" levels={orderBook.sell_levels} side="SELL" />
            </div>
          </section>
          <section className="deco-provenance" aria-labelledby="provenance-title">
            <div><p className="deco-kicker">EVIDENCE</p><h2 id="provenance-title">数据来源</h2></div>
            <dl>
              <Info term="来源" value="人工确认的响应 JSON" />
              <Info term="审核状态" value={orderBook.provenance.review_status} />
              <Info term="请求动作" value="未知 · 不作声明" />
              <Info term="价格合同" value={orderBook.contract.contract_id} />
              <Info term="读取模型" value={orderBook.provenance.read_model_implementation_version} />
            </dl>
          </section>
        </>
      )}
      <section className="deco-deferred"><div><p className="deco-kicker">HISTORY</p><h2>市场历史尚未开放</h2></div><p>当前不展示伪造曲线或推算数据；历史观测满足发布条件后再接入。</p></section>
    </main>
  );
}

function DetailPrices({ orderBook }: { orderBook: CurrentOrderBook }) {
  return (
    <div className="deco-detail-prices" aria-label="当前市场价格">
      <PriceMetric label="最佳买价" value={orderBook.best_buy.canonical_display_text} />
      <PriceMetric label="最佳卖价" value={orderBook.best_sell.canonical_display_text} />
      <PriceMetric label="价差" value={orderBook.spread_display_text} />
      <div className="deco-capture-time"><span>观测时间</span><strong>{formatDateTime(orderBook.captured_at)}</strong><small>{orderBook.contract.currency_code} · contract v{orderBook.contract.contract_version}</small></div>
    </div>
  );
}

function PriceMetric({ label, value }: { label: string; value: string }) {
  return <div className="deco-price-metric"><span>{label}</span><strong>{value}</strong><small>GJN</small></div>;
}

function OrderBookSide({ title, levels, side }: { title: string; levels: CurrentOrderBookLevel[]; side: "BUY" | "SELL" }) {
  return (
    <div className={`deco-book-side ${side.toLowerCase()}`}>
      <h3><span>{side}</span>{title}</h3>
      <div className="deco-book-table">
        <div className="deco-book-head"><span>#</span><span>价格</span><span>数量</span></div>
        {levels.map((level) => <div className="deco-book-row" key={`${side}-${level.level_index}`}><span>{level.level_index + 1}</span><strong>{level.canonical_display_text} GJN</strong><span>{level.quantity}</span></div>)}
      </div>
    </div>
  );
}

function Info({ term, value }: { term: string; value: string }) { return <div><dt>{term}</dt><dd>{value}</dd></div>; }

function ItemError({ error }: { error: ApiError }) {
  return <main className="art-shell"><MarketHeader active="catalog" /><section className="deco-state deco-state-error"><span aria-hidden="true">!</span><div><h1>{error.code === "item_not_found" ? "未找到该载具" : "载具加载失败"}</h1><p>{error.message}</p><Link href="/items">返回载具目录 →</Link></div></section></main>;
}

async function loadItem(itemId: string): Promise<{ item: ItemDetail; orderBook: CurrentOrderBook | null } | { error: ApiError }> {
  try {
    const item = await getItem(itemId);
    try { return { item, orderBook: await getCurrentOrderBook(itemId) }; }
    catch (error) { const display = toDisplayError(error); if (display.code === "order_book_not_found") return { item, orderBook: null }; throw error; }
  } catch (error) { return { error: toDisplayError(error) }; }
}
