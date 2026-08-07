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

/** Cadless brand mark — a four-point spark. Unlike the stroked toolbar glyphs
 * this one is filled so it reads as a solid mark at 16/24/32px; it still
 * inherits `currentColor` and shares the 0 0 16 16 viewBox convention. The
 * concave edges curve toward the centre so the star stays crisp when small,
 * and a small inner facet adds a hint of dimension. */
export const CadlessIcon = ({ size = 16 }: IconProps) => (
  <svg
    className="icon"
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="currentColor"
    stroke="none"
    fillRule="evenodd"
    clipRule="evenodd"
    aria-hidden
  >
    {/* Outer four-point spark; the inner diamond is cut out (evenodd) so the
        facet shows the background and stays crisp on any theme at 16px. */}
    <path d="M8 0.5C8.45 3.7 8.8 5.2 9.8 6.2C10.8 7.2 12.3 7.55 15.5 8C12.3 8.45 10.8 8.8 9.8 9.8C8.8 10.8 8.45 12.3 8 15.5C7.55 12.3 7.2 10.8 6.2 9.8C5.2 8.8 3.7 8.45 0.5 8C3.7 7.55 5.2 7.2 6.2 6.2C7.2 5.2 7.55 3.7 8 0.5ZM8 6.4L6.9 8L8 9.6L9.1 8L8 6.4Z" />
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
