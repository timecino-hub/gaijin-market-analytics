import Link from "next/link";
import { historicalPriceChart } from "../../../lib/historical-price-chart";
import { loadItemDetail } from "../../../lib/item-detail-data";
import type { ApiError, CurrentOrderBook, CurrentOrderBookLevel, ItemHistoricalTrades } from "../../../lib/types";
import { LocalDateTime } from "../../local-date-time";
import { MarketHeader } from "../../market-header";

export const dynamic = "force-dynamic";

type ItemDetailPageProps = { params: Promise<{ itemId: string }> };

export default async function ItemDetailPage({ params }: ItemDetailPageProps) {
  const { itemId } = await params;
  const result = await loadItemDetail(itemId);
  if ("error" in result) return <ItemError error={result.error} />;
  const { item, orderBook, orderBookError, history, historyError } = result;

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
        {orderBook ? <DetailPrices orderBook={orderBook} /> : <div className="deco-detail-no-price"><strong>{orderBookError ? "订单簿读取失败" : "暂无已确认订单簿"}</strong></div>}
      </section>

      {orderBookError ? <section className="deco-error" aria-live="polite"><strong>订单簿读取失败</strong><p>{orderBookError.message}</p></section> : null}
      {orderBook ? (
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
      ) : null}
      <HistorySection itemName={item.name} history={history} error={historyError} />
    </main>
  );
}

function HistorySection({ itemName, history, error }: { itemName: string; history: ItemHistoricalTrades | null; error: ApiError | null }) {
  const chart = historicalPriceChart(history?.buckets ?? []);
  const latest = chart?.points.at(-1);
  return (
    <section className="deco-trade-history" aria-labelledby="history-title">
      <div className="deco-section-heading"><h2 id="history-title">历史成交价格</h2><span>成交日均价 · UTC</span></div>
      {error ? <div className="deco-error" aria-live="polite"><strong>历史记录读取失败</strong><p>{error.message}</p></div> : chart && latest ? (
        <>
          <div className="deco-history-summary"><div><span>最新记录日均价</span><strong>{latest.price} GJN</strong></div><div><span>最新记录日（UTC）</span><strong>{latest.day}</strong></div><div><span>已接入记录日</span><strong>{chart.points.length} 天</strong></div></div>
          <figure className="deco-history-figure">
            <figcaption>{itemName} · 历史日均价</figcaption>
            <div className="deco-history-plot">
              <div className="deco-history-y-labels" aria-hidden="true"><span>{chart.maximum} GJN</span><span>{chart.minimum} GJN</span></div>
              <svg viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={`${itemName}历史日均价，${chart.points[0].day}至${latest.day}，${chart.points.length}个记录日，最低${chart.minimum} GJN，最高${chart.maximum} GJN`}>
                <title>{itemName}历史成交日均价</title>
                {chart.segments.map((segment) => segment.length === 1 ? <circle key={segment[0].day} cx={segment[0].x} cy={segment[0].y} r="3" /> : <polyline key={segment[0].day} points={segment.map((point) => `${point.x},${point.y}`).join(" ")} />)}
                <circle cx={latest.x} cy={latest.y} r="4" />
              </svg>
            </div>
            <div className="deco-history-x-labels"><time dateTime={chart.points[0].day}>{chart.points[0].day}</time><time dateTime={latest.day}>{latest.day}</time></div>
          </figure>
          <details className="deco-history-records"><summary>最近30个记录日</summary><div><table><thead><tr><th scope="col">记录日（UTC）</th><th scope="col">历史日均价（GJN）</th><th scope="col">报告成交量（单位未核实）</th></tr></thead><tbody>{chart.points.slice(-30).reverse().map((point) => <tr key={point.day}><th scope="row"><time dateTime={point.day}>{point.day}</time></th><td>{point.price}</td><td>{point.volume}</td></tr>)}</tbody></table></div></details>
        </>
      ) : <div className="deco-empty-inline"><strong>暂无日均价记录</strong></div>}
    </section>
  );
}

function DetailPrices({ orderBook }: { orderBook: CurrentOrderBook }) {
  return (
    <div className="deco-detail-prices" aria-label="当前市场价格">
      <PriceMetric label="当前买价" value={orderBook.best_buy.canonical_display_text} />
      <PriceMetric label="当前卖价" value={orderBook.best_sell.canonical_display_text} />
      <PriceMetric label="价差" value={orderBook.spread_display_text} />
      <div className="deco-capture-time"><span>观测时间</span><strong><LocalDateTime value={orderBook.captured_at} /></strong><small>{orderBook.contract.currency_code} · contract v{orderBook.contract.contract_version}</small></div>
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
