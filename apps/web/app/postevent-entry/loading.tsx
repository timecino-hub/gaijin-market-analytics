export default function PostEventEntryLoading() {
  return (
    <main className="page-shell">
      <section className="loading-state" aria-live="polite">
        <h1>正在读取活动后入场信号</h1>
        <p>正在按活动目录逐台比对活动前基准与活动后成交价。</p>
      </section>
    </main>
  );
}
