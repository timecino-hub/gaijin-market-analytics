import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gaijin Market Analytics · Preview",
  description: "Confirmed order books with evidence-backed GJN price interpretation."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
