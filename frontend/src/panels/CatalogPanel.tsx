/** Catalog flyout: a browsable panel over the curated benchmark projects (#21).
 *
 * A search box plus domain/category facet chips drive a paginated thumbnail
 * grid; clicking a card opens that project in the workspace (deep-links by
 * project id keep working — opening a card routes exactly like before), and the
 * Customize action (#22) clones an item into an editable copy — chat context
 * seeded from the catalog build transcript — and opens it. Fetches are
 * paginated so the panel stays responsive with hundreds of catalog items. */
import { useEffect, useRef, useState } from "react";

import {
  type CatalogItem,
  type CatalogQuery,
  type CatalogResponse,
  fetchCatalog,
  fetchCatalogOrigins,
} from "../api";
import {
  Chips,
  ConfirmDialog,
  domainIcon,
  EmptyState,
  ItemCard,
  Panel,
  TextInput,
  Tooltip,
} from "../components";
import { API_BASE } from "../config";
import { useApp } from "../useApp";

const PAGE_SIZE = 24;
const SEARCH_DEBOUNCE_MS = 200;

// The one origin a card stays quiet about. `local` is the ordinary case and
// covers the bundled samples, work authored on this machine, and whatever
// catalogue a deployment mounted in their place — one word for all three would
// be a claim about which of them a card is. Which origins exist, and how each is
// spelled, comes from the server: a table here would be a second copy of the
// registry and would disagree the first time a build added a way of arriving.
const UNSPOKEN_ORIGIN = "local";

function CatalogCard({
  item,
  originLabels,
  onRemove,
}: {
  item: CatalogItem;
  originLabels: Record<string, string>;
  onRemove: () => void;
}) {
  const app = useApp();
  const arrived = item.source && item.source !== UNSPOKEN_ORIGIN ? item.source : null;
  // An origin the server named but this build has no label for cannot happen —
  // the labels come from the same registry the source does. An item recorded by
  // a build that is not installed here answers `null` rather than a key, so
  // there is nothing to spell and nothing to guess at.
  const arrivedLabel = arrived ? originLabels[arrived] : undefined;
  return (
    <ItemCard
      title={item.name}
      meta={
        <>
          {item.category ? `${item.category} · ` : ""}
          {item.steps} step{item.steps === 1 ? "" : "s"}
          {/* Only for the items that came from somewhere. Saying "Local" on
              every other card would put a label on the ordinary case and leave
              the ones worth noticing no easier to pick out. */}
          {arrivedLabel && <span className="catalog-card-origin"> · {arrivedLabel}</span>}
        </>
      }
      description={item.description}
      thumbnailUrl={item.thumbnail_url && `${API_BASE}${item.thumbnail_url}`}
      fallbackIcon={domainIcon(item.domain)}
      onOpen={() => void app.selectProject(item.project_id)}
      actions={
        (item.current_version_id != null || item.removable) && (
          <>
            {item.current_version_id != null && (
              <Tooltip label="Clone into an editable copy and start modifying it">
                <button
                  className="catalog-customize"
                  aria-label={`Customize ${item.name}`}
                  onClick={() =>
                    void app.cloneCatalogItem(item.project_id, `${item.name} (copy)`)
                  }
                >
                  Customize
                </button>
              </Tooltip>
            )}
            {/* Not for an item in the catalog the app loads at startup: it
                ships on a read-only mount, the server refuses, and clearing it
                would last until the next start. The two it is offered for are
                an item that arrived here and one whose files are gone — the
                same button, taking quite different things. */}
            {item.removable && (
              <Tooltip
                label={
                  item.files_missing
                    ? "Clear this item's leftover record — its files are already gone"
                    : "Take this received item off this machine"
                }
              >
                <button
                  className="catalog-remove"
                  // Named for what it takes rather than for the word on it. The
                  // label is the whole of what a screen reader is given here,
                  // and "Remove" in front of an item whose files are already
                  // gone describes the wrong act.
                  aria-label={
                    item.files_missing
                      ? `Clear leftover record for ${item.name}`
                      : `Remove ${item.name}`
                  }
                  onClick={onRemove}
                >
                  Remove
                </button>
              </Tooltip>
            )}
          </>
        )
      }
    />
  );
}

export function CatalogPanel() {
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState<string | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [res, setRes] = useState<CatalogResponse | null>(null);
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [error, setError] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [removing, setRemoving] = useState<CatalogItem | null>(null);
  // Bumped after a removal so the effect below refetches: what is in the catalog
  // changed, and the counts and facets with it.
  const [reloads, setReloads] = useState(0);
  // How each origin is spelled, from the registry that decides. Asked once —
  // which origins a build has does not change while it runs. Empty until it
  // answers, and empty if it never does: a card without its origin chip is a
  // card missing one line, while a card labelled from a guess is wrong.
  const [originLabels, setOriginLabels] = useState<Record<string, string>>({});
  const app = useApp();
  // Guards a stale page-1 response from clobbering a newer one (or appended pages).
  const fetchSeq = useRef(0);
  // The query the visible items were fetched with. Load-more pages from this
  // (not live filter state), so a filter change with a base fetch still in
  // flight can never mix result sets at a bogus offset.
  const applied = useRef<CatalogQuery | null>(null);

  useEffect(() => {
    let live = true;
    fetchCatalogOrigins().then(
      (r) => {
        if (!live) return;
        setOriginLabels(Object.fromEntries(r.origins.map((o) => [o.key, o.label])));
      },
      // Left alone rather than retried or surfaced. The listing itself reports
      // its own failure; this one costs a chip on some cards, and a second
      // error message about it would be the louder of the two problems.
      () => {},
    );
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const seq = ++fetchSeq.current;
    const args: CatalogQuery = {
      ...(q ? { q } : {}),
      ...(domain !== null ? { domain } : {}),
      ...(category !== null ? { category } : {}),
      ...(source !== null ? { source } : {}),
      limit: PAGE_SIZE,
    };
    // Debounce keystrokes; filter clicks and first load fetch immediately.
    const t = window.setTimeout(
      () => {
        fetchCatalog(args)
          .then((r) => {
            if (fetchSeq.current !== seq) return;
            applied.current = args;
            setRes(r);
            setItems(r.items);
            setError(false);
          })
          .catch(() => fetchSeq.current === seq && setError(true));
      },
      q ? SEARCH_DEBOUNCE_MS : 0,
    );
    return () => window.clearTimeout(t);
  }, [q, domain, category, source, reloads]);

  async function loadMore() {
    const seq = fetchSeq.current;
    const base = applied.current;
    if (base === null) return; // nothing shown yet
    setLoadingMore(true);
    try {
      const r = await fetchCatalog({ ...base, offset: items.length });
      // Discard if filters changed or a newer page-1 landed mid-flight.
      if (fetchSeq.current !== seq || applied.current !== base) return;
      setRes(r);
      setItems((prev) => [...prev, ...r.items]);
    } catch {
      setError(true);
    } finally {
      setLoadingMore(false);
    }
  }

  function pickDomain(key: string | null) {
    setDomain(key);
    setCategory(null); // categories are scoped to the active domain
  }

  return (
    <Panel title="Catalog" className="catalog-panel">
      <div className="browse-toolbar">
        <TextInput
          type="search"
          aria-label="Search catalog"
          placeholder="Search catalog…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {res && res.domains.length > 1 && (
          <Chips
            facets={res.domains}
            active={domain}
            allLabel="All domains"
            onPick={pickDomain}
          />
        )}
        {res && res.categories.length > 0 && (
          <Chips
            facets={res.categories}
            active={category}
            allLabel="All categories"
            onPick={setCategory}
          />
        )}
        {/* Same rule as the domain chips above, and for the same reason: with
            one kind of origin in the catalog the row filters nothing, and most
            catalogs have one. */}
        {res && res.sources.length > 1 && (
          <Chips facets={res.sources} active={source} allLabel="All sources" onPick={setSource} />
        )}
      </div>

      {error ? (
        <EmptyState>Couldn&apos;t load the catalog.</EmptyState>
      ) : res === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : res.total === 0 ? (
        <EmptyState>
          {q || domain || category || source
            ? "No catalog items match your search."
            : "No catalog items yet."}
        </EmptyState>
      ) : (
        <>
          {res.details_unavailable && (
            // Every item is here, but their tags, categories and thumbnails are
            // not, so the filters look emptier than the catalog is. Saying so
            // beats letting a names-only listing pass for the whole thing.
            <p className="catalog-notice" role="status">
              Item details couldn&apos;t be read, so this list shows names only.
              Re-load the catalog to restore them.
            </p>
          )}
          <div className="item-grid">
            {items.map((it) => (
              <CatalogCard
                key={it.house_id}
                item={it}
                originLabels={originLabels}
                onRemove={() => setRemoving(it)}
              />
            ))}
          </div>
          <div className="browse-foot">
            <span className="catalog-count">
              {items.length} of {res.total}
            </span>
            {items.length < res.total && (
              <button
                className="browse-more"
                onClick={() => void loadMore()}
                disabled={loadingMore}
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            )}
          </div>
        </>
      )}

      <ConfirmDialog
        open={removing !== null}
        title={removing?.files_missing ? "Clear leftover record?" : "Remove catalog item?"}
        // What is being given up differs: one still has its files here, the
        // other has only the record left. Saying the files will be removed
        // would promise something that already happened, and would leave the
        // reason this item cannot simply be reloaded unsaid.
        message={
          removing?.files_missing
            ? `"${removing.name}" has no files left on this machine, so only its ` +
              "record is removed. Clearing it lets you receive the same package again."
            : removing
              ? `"${removing.name}" and its files will be removed from this machine. ` +
                "You can receive the same package again afterwards."
              : ""
        }
        confirmLabel="Remove"
        onConfirm={() => {
          if (removing) {
            void app
              .removeCatalogItem(removing.house_id, removing.project_id)
              .then(() => setReloads((n) => n + 1));
          }
          setRemoving(null);
        }}
        onClose={() => setRemoving(null)}
      />
    </Panel>
  );
}
