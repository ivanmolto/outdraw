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

/** The site header, the way home: the flat mark beside the wordmark's lettering. The brand
 * lockup (wordmark-light.svg) draws its mark at three quarters of the x-height, too small
 * for a 28px header, so the two are composed here from the package's own files: the mark
 * at the lettering's height, and the lockup clipped to its lettering (which starts at
 * x=361 of 1655 and spans y=82…340 of 512). Both files are drawn at 56px and their empty
 * canvas is trimmed with negative margins: the mark's artwork sits at x=111…401,
 * y=138…358 of its 512 tile, so it comes out 24px tall against 28px lettering. */
function Header() {
  return (
    <header className="border-b border-ink/10 bg-paper">
      <div className="mx-auto flex w-full max-w-5xl items-center px-4 py-3">
        <Link href="/" aria-label="Outdraw home" className="inline-flex items-center gap-2.5">
          <span className="inline-flex overflow-hidden">
            <Image
              src="/brand/flat-mark/mark-light.svg"
              alt=""
              width={512}
              height={512}
              priority
              className="-mx-[12px] -mt-[15px] -mb-[17px] h-14 w-14 max-w-none"
            />
          </span>
          <span className="inline-flex overflow-hidden">
            <Image
              src="/brand/wordmark/wordmark-light.svg"
              alt="Outdraw"
              width={1655}
              height={512}
              priority
              className="-mt-[9px] -mb-[19px] -ml-[39px] h-14 w-auto max-w-none"
            />
          </span>
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
