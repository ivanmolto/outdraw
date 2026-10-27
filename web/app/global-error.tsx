"use client";

// The last boundary: an error in the root layout itself. It replaces the layout, so it
// renders its own <html> and <body>; nothing from the error is shown but its digest.
import "./globals.css";

import { PageError } from "@/src/components/page-error";

export default function GlobalError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <html lang="en">
      <body className="flex min-h-full flex-col bg-paper text-ink">
        <PageError error={error} retry={retry} />
      </body>
    </html>
  );
}
