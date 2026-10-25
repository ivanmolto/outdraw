import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Image from "next/image";
import Link from "next/link";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { default: "Outdraw", template: "%s · Outdraw" },
  description: "Book the acts your crowd will turn out for, from Qloo's taste graph.",
  // The brand package (public/brand/README.txt): a theme-aware SVG, the multipage .ico in
  // app/favicon.ico for browsers that want one, and the Apple touch icon.
  icons: {
    icon: [{ url: "/brand/favicon/favicon.svg", type: "image/svg+xml" }],
    apple: "/brand/pwa/apple-touch-icon-180.png",
  },
};

export const viewport: Viewport = {
  themeColor: "#0E0F14",
};

/** The site header: the wordmark (near-black ink, for the off-white ground) as the way home. */
function Header() {
  return (
    <header className="border-b border-ink/10 bg-paper">
      <div className="mx-auto flex w-full max-w-5xl items-center px-4 py-3">
        <Link href="/" aria-label="Outdraw home" className="inline-flex items-center">
          <Image
            src="/brand/wordmark/wordmark-light.svg"
            alt="Outdraw"
            width={1655}
            height={512}
            priority
            className="h-7 w-auto"
          />
        </Link>
      </div>
    </header>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-paper text-ink">
        <Header />
        {children}
      </body>
    </html>
  );
}
