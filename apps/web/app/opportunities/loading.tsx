import { MarketHeader } from "../market-header";

export default function VehiclePotentialLoading() {
  return (
    <main className="art-shell deco-potential-page">
      <MarketHeader active="opportunities" />
      <section className="deco-state" aria-live="polite" aria-busy="true">
        <span className="deco-loader" aria-hidden="true" />
        <div><h1>正在读取载具潜力</h1></div>
      </section>
    </main>
  );
}
