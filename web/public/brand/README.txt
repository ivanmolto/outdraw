OUTDRAW — PRODUCTION BRAND ASSET PACKAGE
========================================
Brand: outdraw / outdraw.live

PALETTE (ONLY THREE COLOURS)
Near-black: #0E0F14
Off-white:  #F6F4EE
Amber:      #F5B400
Dark tiles: amber ink on near-black. Light tiles: near-black ink on off-white.
Transparent flat marks and lockups: off-white for dark surfaces, near-black for light surfaces; amber only on dark surfaces.

TYPEFACE / OUTLINES
Inter Display Medium (500), lowercase “outdraw”, converted to paths with fontTools.
Tracking: -12 font units between glyphs (with a 0.17 lockup scale).
No font files, SVG text elements, external fonts or dependencies shipped.

COMMON GEOMETRY
One 512 × 512 master coordinate square for every mark. App tile radius: 112 / 512.
Arch centreline: M132 290 L132 226 Q132 159 199 159 L313 159 Q380 159 380 226 L380 290.
App and flat mark: 42 source-unit stroke. Favicon: 54 source-unit stroke.
Favicon changes stroke thickness only; same arch path and crowd centre positions.
Crowd centres (160,334), (208,334), (256,334), (304,334), (352,334).
App dot radii: 19,19,24,19,19. At 16 and 24 favicon sizes all dot radii: 21.
Arch span 248 (centreline), height 131; every crowd dot fits within arch’s width.
Mark occupies about 60% of tile width (outer stroke bounds x111…401 = 56.6%).
Dots rest on a common baseline; short legs finish above the crowd.
No extra stage foot: it reduced clarity at 16px in this geometric design.

FOLDERS
app-icon/: 512px tile SVGs, 512px raster and 1024px @2x raster on both palettes.
favicon/: theme-aware favicon.svg, multipage dark favicon.ico (16,32,48), static dark/light 16/32/48/64 SVGs and 1x/2x PNGs.
flat-mark/: isolated SVG artwork on transparency, in off-white, near-black and amber, each 512px/1024px PNG.
wordmark/: isolated mark + outlined “outdraw” lockups on transparent grounds. Suffixless and @1x PNG are native 1655×512; @2x PNG is 3310×1024.
social/: GitHub social artwork in SVG and 1280×640 PNG.
pwa/: 192/512 tiles, 512 maskable tile with reduced mark, 180 Apple touch icon.
ZIP root: copies of every SVG source, for direct repository copying.

QUALITY CHECKS
- Favicon 16px is built from the same arch and circle centres with the heavier 54-unit stroke and equal-size dots. Readability is visually judged, not machine-provable. Intended reading is a proscenium opening plus gathered audience; no concentric arcs or WIFI glyphs.
- All STATIC SVG images consist only of paths, rects, circles (within SVG root and grouping/metadata where needed), with no text, embedded rasters, filters or gradients. EXCEPTION: the theme-aware favicon.svg includes CSS style and groups to support prefers-color-scheme switching, per spec.
- PNG sizes, palettes, and transparent/background expectations are validated by the build script.
- No amber pixels appear in assets with off-white backgrounds.
- All outline geometry shared from the same master.
