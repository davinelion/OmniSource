import type { Metadata, Viewport } from "next";
import "./globals.css";
import Nav from "@/components/Nav";
import PwaRegister from "@/components/PwaRegister";
import { getApps, getGeneratedAt, getSources } from "@/lib/data";
import { getLangDict, isRtl } from "@/lib/lang";

// Canonical origin for absolutised metadata. Deployments can override it with
// NEXT_PUBLIC_SITE_URL; the default matches where the catalog is published.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://iamsmmh.github.io/OmniSource";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "OmniSource — verified source catalog", template: "%s · OmniSource" },
  description:
    "One feed for AltStore, SideStore, Feather, ESign and LiveContainer. Every app validated, every release resolved from its official upstream.",
  applicationName: "OmniSource",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "OmniSource", statusBarStyle: "default" },
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: "OmniSource",
    title: "OmniSource — verified source catalog",
    description: "One feed for AltStore, SideStore, Feather, ESign and LiveContainer.",
    url: "/",
    images: [{ url: "/icon.svg", width: 512, height: 512, alt: "OmniSource" }],
  },
  twitter: {
    card: "summary",
    title: "OmniSource — verified source catalog",
    description: "One feed for AltStore, SideStore, Feather, ESign and LiveContainer.",
    images: ["/icon.svg"],
  },
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#09090b" },
  ],
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const { lang, dict } = await getLangDict();
  const apps = getApps().length;
  const sources = getSources().length;
  const synced = getGeneratedAt();
  return (
    <html lang={lang} dir={isRtl(lang) ? "rtl" : "ltr"}>
      <body className="min-h-screen bg-zinc-50 text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        <PwaRegister lang={lang} />
        <Nav dict={dict} lang={lang} />
        <main id="main" className="mx-auto w-full max-w-6xl px-4 py-8">
          {children}
        </main>
        <footer className="border-t border-zinc-200 py-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          OmniSource · {apps} {dict.common.apps} · {sources} {dict.common.sources}
          {synced && (
            <>
              {" "}
              · {dict.common.lastSync} {synced.slice(0, 10)}
            </>
          )}
        </footer>
      </body>
    </html>
  );
}
