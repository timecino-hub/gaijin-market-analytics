export default function OpportunitiesLoading() {
  return (
    <main className="page-shell">
      <section className="loading-state" aria-live="polite">
        <h1>正在计算机会排行榜</h1>
        <p>正在读取同一时间窗内的已导入快照并运行确定性评分。</p>
      </section>
    </main>
  );
}
