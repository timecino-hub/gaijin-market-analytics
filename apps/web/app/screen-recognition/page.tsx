import Link from "next/link";
import { ScreenRecognitionWorkspace } from "./screen-recognition-workspace";

export default function ScreenRecognitionPage() {
  return (
    <main className="page-shell screen-recognition-page">
      <header className="page-header">
        <nav className="page-nav" aria-label="页面导航">
          <Link href="/" className="back-link">
            返回首页
          </Link>
          <Link href="/items" className="back-link">
            浏览商品
          </Link>
        </nav>
        <p className="eyebrow">Local screen recognition</p>
        <h1>屏幕识别人工复核</h1>
        <p>
          手动上传 current 市场页截图，在本机 OCR 后创建内存复核记录。不会访问 Gaijin Market、
          不读取 Cookie、不写数据库、不生成 CSV。
        </p>
      </header>

      <ScreenRecognitionWorkspace />
    </main>
  );
}
