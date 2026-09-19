import Link from "next/link";
import { getCurrentOrderBook, getItem, toDisplayError } from "../../../lib/api-client";
import { formatDateTime } from "../../../lib/formatters";
import type { ApiError, CurrentOrderBook, CurrentOrderBookLevel, ItemDetail } from "../../../lib/types";

export const dynamic = "force-dynamic";

type ItemDetailPageProps = { params: Promise<{ itemId: string }> };

export default async function ItemDetailPage({ params }: ItemDetailPageProps) {
  const { itemId } = await params;
  const result = await loadItem(itemId);
  if ("error" in result) return <ItemError error={result.error} />;
  const { item, orderBook } = result;

  return (
    <main className="market-shell detail-page">
      <header className="site-header">
        <Link className="brand" href="/">Gaijin Market Analytics</Link>
        <nav><Link href="/items">Market</Link><span aria-disabled="true">Analytics · coming soon</span></nav>
        <span className="preview-badge">Read-only preview</span>
      </header>
      <div className="detail-breadcrumb"><Link href="/items">Market</Link><span>/</span><span>{item.name}</span></div>
      <section className="item-title-block">
        <div><p className="eyebrow">{item.category}</p><h1>{item.name}</h1><p>{item.external_key}</p></div>
        <span className={orderBook?.freshness === "fresh" ? "freshness fresh" : "freshness stale"}>{orderBook ? orderBook.freshness : "No order book"}</span>
      </section>

      {!orderBook ? (
        <div className="market-empty"><strong>No confirmed order book</strong><p>This item exists, but no approved current-book capture is available.</p></div>
      ) : (
        <>
          <section className="price-summary" aria-label="Current market prices">
            <PriceMetric label="Best buy" value={orderBook.best_buy.canonical_display_text} tone="buy" />
            <PriceMetric label="Best sell" value={orderBook.best_sell.canonical_display_text} tone="sell" />
            <PriceMetric label="Spread" value={orderBook.spread_display_text} tone="neutral" />
            <div className="price-meta"><span>Captured</span><strong>{formatDateTime(orderBook.captured_at)}</strong><small>{orderBook.contract.currency_code} · contract v{orderBook.contract.contract_version}</small></div>
          </section>
          <section className="orderbook-section" aria-labelledby="orderbook-title">
            <div className="market-section-heading"><div><p className="eyebrow">Current depth</p><h2 id="orderbook-title">Order book</h2></div><span>{orderBook.schema_version}</span></div>
            <div className="orderbook-grid">
              <OrderBookSide title="BUY orders" levels={orderBook.buy_levels} side="BUY" />
              <OrderBookSide title="SELL orders" levels={orderBook.sell_levels} side="SELL" />
            </div>
          </section>
          <section className="provenance-band" aria-labelledby="provenance-title">
            <div><p className="eyebrow">Evidence</p><h2 id="provenance-title">Data provenance</h2></div>
            <dl><Info term="Source" value="Confirmed manual response JSON" /><Info term="Review" value={orderBook.provenance.review_status} /><Info term="Request action" value="Unknown · not claimed" /><Info term="Price contract" value={orderBook.contract.contract_id} /><Info term="Read model" value={orderBook.provenance.read_model_implementation_version} /></dl>
          </section>
        </>
      )}
      <section className="market-section muted-band"><div><p className="eyebrow">History</p><h2>Market history</h2></div><p>Historical market data is not available yet.</p></section>
    </main>
  );
}

function PriceMetric({ label, value, tone }: { label: string; value: string; tone: "buy" | "sell" | "neutral" }) {
  return <div className={`price-metric ${tone}`}><span>{label}</span><strong>{value}</strong><small>GJN</small></div>;
}

function OrderBookSide({ title, levels, side }: { title: string; levels: CurrentOrderBookLevel[]; side: "BUY" | "SELL" }) {
  return <div className={`book-side ${side.toLowerCase()}`}><h3>{title}</h3><div className="book-table"><div className="book-head"><span>#</span><span>Price</span><span>Quantity</span></div>{levels.map((level) => <div className="book-row" key={`${side}-${level.level_index}`}><span>{level.level_index + 1}</span><strong>{level.canonical_display_text} GJN</strong><span>{level.quantity}</span></div>)}</div></div>;
}

function Info({ term, value }: { term: string; value: string }) { return <div><dt>{term}</dt><dd>{value}</dd></div>; }

function ItemError({ error }: { error: ApiError }) { return <main className="market-shell detail-page"><header className="site-header"><Link className="brand" href="/">Gaijin Market Analytics</Link><Link href="/items">Market</Link></header><div className="market-error"><strong>{error.code === "item_not_found" ? "Item not found" : "Unable to load item"}</strong><p>{error.message}</p></div></main>; }

async function loadItem(itemId: string): Promise<{ item: ItemDetail; orderBook: CurrentOrderBook | null } | { error: ApiError }> {
  try {
    const item = await getItem(itemId);
    try { return { item, orderBook: await getCurrentOrderBook(itemId) }; }
    catch (error) { const display = toDisplayError(error); if (display.code === "order_book_not_found") return { item, orderBook: null }; throw error; }
  } catch (error) { return { error: toDisplayError(error) }; }
}
