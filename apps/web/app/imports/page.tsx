import Link from "next/link";
import { CsvImportForm } from "./csv-import-form";

export default function ImportsPage() {
  return (
    <main className="page-shell import-page">
      <header className="page-header">
        <Link href="/" className="back-link">
          返回首页
        </Link>
        <p className="eyebrow">CSV import</p>
        <h1>CSV 数据导入</h1>
        <p>
          上传手动准备、导入或明确授权来源的 CSV 文件。浏览器只做文件名和大小预检查，后端仍是最终校验方。
        </p>
      </header>

      <CsvImportForm />
    </main>
  );
}
