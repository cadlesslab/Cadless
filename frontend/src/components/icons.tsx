/** Inline SVG icons — a small, dependency-free set tuned to the toolbar/menus.
 * All inherit `currentColor` and a consistent 1.5 stroke; size defaults to 16. */
import type { ReactNode } from "react";

function Svg({ size = 16, children }: { size?: number; children: ReactNode }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

type IconProps = { size?: number };

/** Gear — Settings panel. Center hub + eight spokes, matching the stroked 16×16 toolbar
 * convention. */
export const SettingsIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <circle cx="8" cy="8" r="2.25" />
    <path d="M8 1.4v2.1M8 12.5v2.1M1.4 8h2.1M12.5 8h2.1M3.35 3.35l1.5 1.5M11.15 11.15l1.5 1.5M12.65 3.35l-1.5 1.5M4.85 11.15l-1.5 1.5" />
  </Svg>
);

/** Cadless brand mark — an isometric solid. Unlike the stroked toolbar glyphs
 * this one is filled so it reads as a solid mark at 16/24/32px, and it still
 * inherits `currentColor`.
 *
 * It keeps its own 0 0 24 24 viewBox rather than the 16×16 the glyphs above
 * share, because the mark is drawn in more than one place and re-fitting path
 * data to another grid by hand is how those copies stop matching. A viewBox is
 * a coordinate space; nothing renders differently for it. `brandMark.test.ts`
 * is what holds the copies together. */
export const CadlessIcon = ({ size = 16 }: IconProps) => (
  <svg
    className="icon"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    stroke="none"
    aria-hidden
  >
    {/* The lit top face, with the centre cut out (evenodd) so the solid reads as
        hollow rather than as a flat tile. */}
    <path
      fillRule="evenodd"
      d="M12 2.5 21 7.75 12 13 3 7.75Z M8.9 7.75a3.1 1.8 0 1 0 6.2 0 3.1 1.8 0 1 0-6.2 0Z"
    />
    {/* The two side faces. They are held apart by opacity alone, which is what
        keeps the whole mark on one `currentColor` and so correct in either
        theme without a second declaration. */}
    <path d="M3 7.75 12 13v8.5L3 16.25Z" opacity={0.48} />
    <path d="M21 7.75 12 13v8.5l9-5.25Z" opacity={0.72} />
  </svg>
);

export const ChevronDownIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M4 6l4 4 4-4" />
  </Svg>
);

export const FolderIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.8a1 1 0 0 1 .8.4l.6.85a1 1 0 0 0 .8.4h4A1.5 1.5 0 0 1 14 6.55v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5z" />
  </Svg>
);

export const CubeIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M8 1.7l5.4 3.1v6.4L8 14.3 2.6 11.2V4.8z" />
    <path d="M2.7 4.9L8 7.95l5.3-3.05" />
    <path d="M8 7.95V14.1" />
  </Svg>
);

export const SunIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <circle cx="8" cy="8" r="3" />
    <path d="M8 1.5v1.4M8 13.1v1.4M1.5 8h1.4M13.1 8h1.4M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1" />
  </Svg>
);

export const MoonIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M13.2 9.6A5.6 5.6 0 1 1 6.4 2.8a4.4 4.4 0 0 0 6.8 6.8z" />
  </Svg>
);

export const HelpIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <circle cx="8" cy="8" r="6.3" />
    <path d="M6.1 6.2a1.9 1.9 0 0 1 3.5 1c0 1.2-1.6 1.4-1.6 2.5" />
    <path d="M8 11.6v.01" />
  </Svg>
);

export const InfoIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <circle cx="8" cy="8" r="6.3" />
    <path d="M8 7.3v4" />
    <path d="M8 4.9v.01" />
  </Svg>
);

export const SlidersIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M2 4.5h6M12 4.5h2" />
    <circle cx="10" cy="4.5" r="1.5" />
    <path d="M2 11.5h2M8 11.5h6" />
    <circle cx="6" cy="11.5" r="1.5" />
  </Svg>
);

export const HistoryIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <circle cx="8" cy="8" r="6.3" />
    <path d="M8 4.6V8l2.4 1.4" />
  </Svg>
);

export const CloseIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </Svg>
);

/** Catalog rail glyph — stacked cards / library. */
export const CatalogIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <rect x="2.5" y="2.5" width="11" height="3" rx="0.8" />
    <rect x="2.5" y="6.75" width="11" height="3" rx="0.8" />
    <rect x="2.5" y="11" width="11" height="3" rx="0.8" />
  </Svg>
);

/** Arrow into a tray — bringing a package in from a file. Not a folder, which
 * would read as somewhere to browse: what arrives here is one package someone
 * handed over, and it arrives without an account. */
export const ImportIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M8 2.5v6" />
    <path d="M5.5 6.25 8 8.75l2.5-2.5" />
    <path d="M3 10.75v1.75a0.75 0.75 0 0 0 .75.75h8.5a0.75 0.75 0 0 0 .75-.75v-1.75" />
  </Svg>
);

/** House glyph for the catalog "House" group. */
export const HouseIcon = ({ size }: IconProps) => (
  <Svg size={size}>
    <path d="M2.5 7.5 8 3l5.5 4.5" />
    <path d="M3.8 8.6v4.4a.6.6 0 0 0 .6.6h7.2a.6.6 0 0 0 .6-.6V8.6" />
    <path d="M6.6 13.6V10h2.8v3.6" />
  </Svg>
);

/** The icon a card shows where it has no picture.
 *
 * Domains are open-ended and icons are not, so this is a small map with a
 * fallback rather than a promise to have one for every key. Offered through the
 * plugin contract because any panel that renders a card needs the same answer
 * for the same domain — a picture missing in a panel that ships from elsewhere
 * should not look like a different kind of missing than one missing here.
 */
export function domainIcon(domain: string | null | undefined): ReactNode {
  if (domain === "house") return <HouseIcon />;
  return <CubeIcon />;
}
