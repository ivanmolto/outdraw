import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Geist, Geist_Mono } from "next/font/google";
import Image from "next/image";
import Link from "next/link";
import { Suspense } from "react";

import { NavLinks, PrimaryNav } from "@/src/components/primary-nav";
import { THEME_SCRIPT, ThemeToggle } from "@/src/components/theme-toggle";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/** The one display face: page titles, big numerals and the landing's headings. */
const display = Bricolage_Grotesque({
  variable: "--font-display",
  subsets: ["latin"],
  axes: ["opsz"],
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
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F6F4EE" },
    { media: "(prefers-color-scheme: dark)", color: "#0E0F14" },
  ],
};

/** The lockup, the way home: the flat mark beside the wordmark's lettering, in the cut for
 * the theme (near-black on paper, off-white on the dark stage). The brand lockup
 * (wordmark-*.svg) draws its mark at three quarters of the x-height, too small for a 28px
 * header, so the two are composed here from the package's own files: the mark at the
 * lettering's height, and the lockup clipped to its lettering (which starts at x=361 of
 * 1655 and spans y=82…340 of 512). Both files are drawn at 56px and their empty canvas is
 * trimmed with negative margins: the mark's artwork sits at x=111…401, y=138…358 of its
 * 512 tile, so it comes out 24px tall against 28px lettering. */
function Lockup({ cut }: { cut: "light" | "dark" }) {
  return (
    <span className={`items-center gap-2.5 ${cut === "light" ? "theme-light-only" : "theme-dark-only"}`}>
      <span className="inline-flex overflow-hidden">
        <Image
          src={`/brand/flat-mark/mark-${cut}.svg`}
          alt=""
          width={512}
          height={512}
          priority
          className="-mx-[12px] -mt-[15px] -mb-[17px] h-14 w-14 max-w-none"
        />
      </span>
      <span className="inline-flex overflow-hidden">
        <Image
          src={`/brand/wordmark/wordmark-${cut}.svg`}
          alt="Outdraw"
          width={1655}
          height={512}
          priority
          className="-mt-[9px] -mb-[19px] -ml-[39px] h-14 w-auto max-w-none"
        />
      </span>
    </span>
  );
}

function Header() {
  return (
    <header className="border-b border-line bg-paper">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-4">
        <Link href="/" aria-label="Outdraw home" className="inline-flex items-center">
          <Lockup cut="light" />
          <Lockup cut="dark" />
        </Link>
        <nav aria-label="Primary" className="flex items-center gap-4 sm:gap-6">
          {/* The current page underlined; the fallback is the same links, uncounted. */}
          <Suspense fallback={<NavLinks pathname={null} />}>
            <PrimaryNav />
          </Suspense>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="border-t border-line bg-paper">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-8 text-sm text-ink/60 sm:flex-row sm:items-center sm:justify-between">
        <p>
          <span className="font-medium text-ink">Outdraw</span> · booking intelligence for
          live-music venues, built on Qloo&apos;s taste graph.
        </p>
        <p>Aggregate taste affinities only. Nothing about individuals, ever.</p>
      </div>
    </footer>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} ${display.variable} h-full`}
    >
      <head>
        {/* The stored theme, applied before the first paint (src/components/theme-toggle.tsx). */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col bg-paper text-ink">
        <Header />
        {children}
        <Footer />
      </body>
    </html>
  );
}
