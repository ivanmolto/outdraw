"use client";

// The header's links, with the current page's underlined (`aria-current="page"`): ink on
// the light page, amber on the dark one (the `spot` token). Reading the pathname makes this
// a client component; the layout renders it inside <Suspense> with the same links as the
// fallback, so the static shell of every page stays static.
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/#demos", path: "/", label: "Demo venues", className: "hidden sm:inline" },
  { href: "/venue/new", path: "/venue/new", label: "Set up a venue", className: "" },
] as const;

export function NavLinks({ pathname }: { pathname: string | null }) {
  return (
    <>
      {LINKS.map((link) => {
        const current = pathname === link.path;
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={current ? "page" : undefined}
            className={`${link.className} border-b-2 pb-0.5 text-sm font-medium whitespace-nowrap ${
              current ? "border-spot text-ink" : "border-transparent text-ink/70 hover:text-ink"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </>
  );
}

export function PrimaryNav() {
  return <NavLinks pathname={usePathname()} />;
}
