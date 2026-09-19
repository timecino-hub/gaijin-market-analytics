import Link from "next/link";

export default function NotFound() {
  return <main className="market-shell"><div className="market-empty"><strong>404 · Page not found</strong><p>The requested market page does not exist.</p><Link href="/">Return home</Link></div></main>;
}
