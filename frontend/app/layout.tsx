import type { Metadata, Viewport } from "next";
import "./globals.css";
import { StoreProvider } from "@/lib/store";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "КООРАДАР",
  description: "Дашборд-конструктор координатора массовых инцидентов МТС",
  icons: { icon: [{ url: "/brand/favicon-32.png", sizes: "32x32" }, { url: "/brand/favicon-192.png", sizes: "192x192" }], apple: "/brand/apple-touch-icon.png" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#ffffff" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Расширения браузера (кошельки, переводчики) дописывают атрибуты в <html> до загрузки React.
    // suppressHydrationWarning глушит предупреждение только для атрибутов самого <html>, не для дочерних узлов.
    <html lang="ru" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Unbounded:wght@500&family=JetBrains+Mono:wght@400;500&display=swap"
        />
      </head>
      <body>
        <StoreProvider>
          <AppShell>{children}</AppShell>
        </StoreProvider>
      </body>
    </html>
  );
}
