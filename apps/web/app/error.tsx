"use client";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <main className="market-shell"><div className="market-error"><strong>Something went wrong</strong><p>The page could not be loaded. The API may be unavailable.</p><button onClick={reset}>Try again</button></div></main>;
}
