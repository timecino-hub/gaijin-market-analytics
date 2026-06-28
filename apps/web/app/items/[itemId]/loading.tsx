export default function ItemDetailLoading() {
  return (
    <main className="page-shell">
      <section className="loading-state" aria-live="polite">
        <h1>正在加载商品详情</h1>
        <p>正在读取商品基础信息和历史快照。</p>
      </section>
    </main>
  );
}
