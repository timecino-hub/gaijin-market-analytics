export default function ItemsLoading() {
  return (
    <main className="page-shell">
      <section className="loading-state" aria-live="polite">
        <h1>正在加载商品</h1>
        <p>正在从本地 API 读取已导入市场数据。</p>
      </section>
    </main>
  );
}
