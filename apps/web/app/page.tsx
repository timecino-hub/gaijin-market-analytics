export default function Home() {
  return (
    <main className="page">
      <section className="intro" aria-labelledby="page-title">
        <p className="eyebrow">Engineering scaffold</p>
        <h1 id="page-title">Gaijin Market Analytics</h1>
        <p>
          A compliant foundation for descriptive analytics using CSV, JSON,
          manual, or explicitly authorized market data sources.
        </p>
      </section>
      <section className="status" aria-label="Project status">
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
