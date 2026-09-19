import Link from "next/link";
import { getItems, toDisplayError } from "../lib/api-client";

export const dynamic = "force-dynamic";

export default async function Home() {
  const result = await loadRecentItems();

  return (
    <main className="market-shell">
      <header className="site-header">
        <Link className="brand" href="/">Gaijin Market Analytics</Link>
        <nav aria-label="Primary navigation">
          <Link href="/items">Market</Link>
          <span aria-disabled="true">Analytics · coming soon</span>
        </nav>
        <span className="preview-badge">Read-only preview</span>
      </header>

      <section className="market-hero" aria-labelledby="home-title">
        <div>
          <p className="eyebrow">Authorized market data</p>
          <h1 id="home-title">Gaijin Market Analytics</h1>
          <p className="hero-copy">
            Browse confirmed order books with versioned, evidence-backed GJN price interpretation.
          </p>
        </div>
        <form className="hero-search" action="/items">
          <label htmlFor="home-search">Find an item</label>
          <div>
            <input id="home-search" name="search" type="search" placeholder="Name or external key" />
            <button type="submit">Search market</button>
          </div>
        </form>
      </section>

      <section className="status-strip" aria-label="Service and data status">
        <div><span>API</span><strong className={result.ok ? "status-live" : "status-down"}>{result.ok ? "Online" : "Unavailable"}</strong></div>
        <div><span>Data policy</span><strong>Confirmed imports only</strong></div>
        <div><span>Price contract</span><strong>GJN current-book v1</strong></div>
        <div><span>Live polling</span><strong>Disabled</strong></div>
      </section>

      <section className="market-section" aria-labelledby="recent-title">
        <div className="market-section-heading">
          <div><p className="eyebrow">Browse</p><h2 id="recent-title">Recently available items</h2></div>
          <Link href="/items">View all items</Link>
        </div>
        {!result.ok ? (
          <div className="market-error"><strong>API unavailable</strong><p>{result.message}</p></div>
        ) : result.items.length === 0 ? (
          <div className="market-empty"><strong>No imported items yet</strong><p>Only approved, explicitly imported data will appear here.</p></div>
        ) : (
          <div className="item-browser-grid">
            {result.items.map((item) => (
              <Link className="item-row" href={`/items/${item.id}`} key={item.id}>
                <div><strong>{item.name}</strong><span>{item.external_key}</span></div>
                <span>{item.category}</span>
                <span className="row-action">Open</span>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="market-section muted-band" aria-labelledby="history-title">
        <div><p className="eyebrow">Historical market</p><h2 id="history-title">Price history</h2></div>
        <p>Historical market data is not available yet.</p>
      </section>
    </main>
  );
}

async function loadRecentItems() {
  try {
    const data = await getItems({ page: "1", page_size: "6", sort: "updated_at", order: "desc" });
    return { ok: true as const, items: data.items };
  } catch (error) {
    return { ok: false as const, items: [], message: toDisplayError(error).message };
  }
}
