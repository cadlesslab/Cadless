/** The card and the chips that browsing a collection of models is made of.
 *
 * More than one panel browses: the catalog on this machine, and whatever a
 * build adds that reads a listing somewhere else. They ask different servers
 * different questions and end up rendering the same thing —
 * a grid of pictures with names under them, narrowed by chips above. Written
 * once because a copy would drift, and the first thing to drift would be the
 * part a person notices: two lists in one app that no longer look alike.
 *
 * What differs between them stays outside: where a thumbnail is fetched from,
 * what the meta line says, which actions a card carries. Those are arguments.
 */
import { type ReactNode, useState } from "react";

/** One chip's worth of filter.
 *
 * `count` is optional because not every caller can know it. The local catalog
 * is counted whole by the server before it answers; a listing somewhere else
 * is read a page at a time behind an opaque cursor, so a number beside one of
 * its chips could only ever be "how many of these are on screen", which is
 * not what a count next to a filter says.
 */
export interface Facet {
  key: string;
  label: string;
  count?: number;
}

export function Chips({
  facets,
  active,
  allLabel,
  onPick,
}: {
  facets: Facet[];
  active: string | null;
  allLabel: string;
  onPick: (key: string | null) => void;
}) {
  return (
    <div className="chips" role="group" aria-label={allLabel}>
      <button
        className={`chip ${active === null ? "on" : ""}`}
        aria-pressed={active === null}
        onClick={() => onPick(null)}
      >
        {allLabel}
      </button>
      {facets.map((f) => (
        <button
          key={f.key}
          className={`chip ${active === f.key ? "on" : ""}`}
          aria-pressed={active === f.key}
          onClick={() => onPick(active === f.key ? null : f.key)}
        >
          {f.count === undefined ? f.label : `${f.label} (${f.count})`}
        </button>
      ))}
    </div>
  );
}

export function ItemCard({
  title,
  meta,
  description,
  thumbnailUrl,
  fallbackIcon,
  onOpen,
  actions,
  disabled,
}: {
  title: string;
  meta: ReactNode;
  description?: string | null;
  /** Already absolute, or already prefixed with `API_BASE` — this component
   * does not know which server the picture came from. */
  thumbnailUrl?: string | null;
  /** Shown where there is no picture, and where one failed to arrive. */
  fallbackIcon: ReactNode;
  onOpen: () => void;
  actions?: ReactNode;
  /** Whether opening this card is refused for now. A grid whose actions are
   * held while a request is in the air but whose cards still open is only
   * half held: opening one is itself a request, and finishing it would report
   * the panel free while the first one is still out. Optional, so a grid with
   * nothing to hold passes nothing. */
  disabled?: boolean;
}) {
  // A picture fetched from elsewhere crosses a network to get here, so unlike
  // a local thumbnail it can be named and still not arrive. Falling back to the same
  // placeholder keeps a slow or broken image from reading as a broken card —
  // and the frame holds its shape either way, because the thumbnail slot is a
  // fixed square whether or not anything ever fills it.
  //
  // What failed rather than that something did: a grid reuses a card as its
  // contents change — another page, a re-search, a listing that published a new
  // version — and a card remembering only "broken" would refuse every picture
  // after the first bad one.
  const [failed, setFailed] = useState<string | null>(null);
  const picture = thumbnailUrl && failed !== thumbnailUrl;
  return (
    <div className="item-card">
      <button className="item-card-open" aria-label={title} onClick={onOpen} disabled={disabled}>
        <span className="item-thumb" aria-hidden>
          {picture ? (
            <img
              src={thumbnailUrl}
              alt=""
              loading="lazy"
              onError={() => setFailed(thumbnailUrl)}
            />
          ) : (
            <span className="item-thumb-placeholder">{fallbackIcon}</span>
          )}
        </span>
        <span className="item-card-name">{title}</span>
        <span className="item-card-meta">{meta}</span>
        {description && <span className="item-card-desc">{description}</span>}
      </button>
      {actions && <span className="item-card-actions">{actions}</span>}
    </div>
  );
}
