import type { Metadata } from "next";
import "./globals.css";
import "./art-direction.css";

export const metadata: Metadata = {
  title: "Gaijin Market Analytics · 只读预览",
  description: "经审核的 Gaijin 市场载具目录与当前订单簿。"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
