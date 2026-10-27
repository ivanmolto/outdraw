import type { MetadataRoute } from "next";

// The web app manifest (T45): what Chrome on Android and Safari on iOS read to offer
// "Add to Home Screen" and to open the installed app without browser chrome. Next serves it
// at /manifest.webmanifest and links it from every page. The tiles come from the brand
// package (public/brand/README.txt, `pwa/`): the dark cut, since the manifest takes one
// colour set and the installed app opens on the dark stage before the theme script runs.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Outdraw",
    short_name: "Outdraw",
    description: "Book the acts your crowd will turn out for, from Qloo's taste graph.",
    start_url: "/",
    display: "standalone",
    background_color: "#0E0F14",
    theme_color: "#0E0F14",
    icons: [
      { src: "/brand/pwa/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/brand/pwa/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: "/brand/pwa/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
