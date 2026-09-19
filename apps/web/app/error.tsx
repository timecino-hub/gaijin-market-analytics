"use client";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <main className="art-shell"><section className="deco-state deco-state-error"><span aria-hidden="true">!</span><div><h1>页面加载失败</h1><p>只读 API 可能暂时不可用。</p><button onClick={reset}>重新加载</button></div></section></main>;
}
