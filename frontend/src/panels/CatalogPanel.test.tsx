import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { renderWithProviders } from "../test/utils";
import { CatalogPanel } from "./CatalogPanel";

const item = (over: Partial<api.CatalogItem>): api.CatalogItem => ({
  house_id: "x",
  name: "X",
  project_id: 1,
  current_version_id: 10,
  steps: 3,
  domain: "house",
  category: null,
  tags: [],
  description: null,
  thumbnail_url: null,
  removable: false,
  files_missing: false,
  source: "local",
  ...over,
});

const ITEMS: api.CatalogItem[] = [
  item({
    house_id: "zillow-1", name: "Zillow One", project_id: 11,
    current_version_id: 100, steps: 16, category: "bungalow",
    tags: ["garage"], description: "Cosy bungalow.",
    thumbnail_url: "/versions/100/artifacts/thumbnail",
  }),
  item({
    house_id: "flanged-shaft", name: "Flanged Shaft", project_id: 21,
    current_version_id: 200, steps: 4, domain: "mechanical",
    removable: true, source: "depot",
  }),
];

const RESPONSE: api.CatalogResponse = {
  groups: [],
  items: ITEMS,
  total: 2,
  limit: 24,
  offset: 0,
  domains: [
    { key: "house", label: "House", count: 1 },
    { key: "mechanical", label: "Mechanical", count: 1 },
  ],
  categories: [{ key: "bungalow", label: "bungalow", count: 1 }],
  sources: [
    { key: "local", label: "Local", count: 1 },
    { key: "depot", label: "Depot", count: 1 },
  ],
};

vi.mock("../api", async (orig) => {
  const actual = await orig<typeof import("../api")>();
  return {
    ...actual,
    listVersions: vi.fn(async () => []),
    listMessages: vi.fn(async () => []),
    listProjects: vi.fn(async () => []),
    cloneProject: vi.fn(async (_pid: number, name?: string) => ({
      id: 999, name: name ?? "clone", created_at: "", updated_at: "", current_version_id: null,
    })),
    fetchCatalog: vi.fn(async () => RESPONSE),
    // How each origin is spelled comes from the server now, so a panel that is
    // not told cannot label a chip. Answered here with what this build ships
    // plus one an assembled build registers, which is what the app sees.
    fetchCatalogOrigins: vi.fn(async () => ({
      origins: [
        { key: "local", label: "Local" },
        { key: "depot", label: "Depot" },
        { key: "file", label: "File" },
      ],
    })),
    removeCatalogItem: vi.fn(async () => {}),
  };
});

const RECEIVED = item({
  house_id: "recv-1", name: "Received One", project_id: 31, removable: true,
});

const fetchCatalog = vi.mocked(api.fetchCatalog);

afterEach(() => {
  vi.clearAllMocks();
  // clearAllMocks drops recorded calls but keeps implementations, so a test that
  // narrowed the catalog would otherwise hand its response to the next one.
  fetchCatalog.mockResolvedValue(RESPONSE);
});

describe("CatalogPanel", () => {
  it("renders a card per item with thumbnail and metadata", async () => {
    renderWithProviders(<CatalogPanel />);
    expect(await screen.findByRole("button", { name: "Zillow One" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Flanged Shaft" })).toBeInTheDocument();
    // the baked thumbnail is shown; items without one get a placeholder
    const img = document.querySelector(".item-thumb img");
    expect(img).toHaveAttribute("src", expect.stringContaining("/versions/100/artifacts/thumbnail"));
    expect(screen.getByText("Cosy bungalow.")).toBeInTheDocument();
  });

  it("says so when the listing is names only", async () => {
    // A catalog whose item details could not be read still lists its items, so
    // without this the user sees a stripped catalog and no reason for it.
    fetchCatalog.mockResolvedValueOnce({ ...RESPONSE, details_unavailable: true });
    renderWithProviders(<CatalogPanel />);
    await screen.findByRole("button", { name: "Zillow One" });
    expect(await screen.findByRole("status")).toHaveTextContent(/names only/i);
  });

  it("shows no such notice for an ordinary catalog", async () => {
    renderWithProviders(<CatalogPanel />);
    await screen.findByRole("button", { name: "Zillow One" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("searches with the query box", async () => {
    renderWithProviders(<CatalogPanel />);
    await screen.findByRole("button", { name: "Zillow One" });
    fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), {
      target: { value: "zil" },
    });
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(expect.objectContaining({ q: "zil" })),
    );
  });

  it("filters by domain via the facet chips", async () => {
    renderWithProviders(<CatalogPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /House \(1\)/ }));
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(
        expect.objectContaining({ domain: "house" }),
      ),
    );
    // toggling back to All drops the filter
    fireEvent.click(screen.getByRole("button", { name: /All domains/ }));
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(
        expect.not.objectContaining({ domain: expect.anything() }),
      ),
    );
  });

  it("filters by category via the facet chips", async () => {
    renderWithProviders(<CatalogPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /bungalow \(1\)/ }));
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(
        expect.objectContaining({ category: "bungalow" }),
      ),
    );
  });

  it("filters by where an item came from via the facet chips", async () => {
    renderWithProviders(<CatalogPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /Depot \(1\)/ }));
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(expect.objectContaining({ source: "depot" })),
    );
    fireEvent.click(screen.getByRole("button", { name: /All sources/ }));
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(
        expect.not.objectContaining({ source: expect.anything() }),
      ),
    );
  });

  it("offers no source chips when everything came from the same place", async () => {
    // A row with one chip in it filters nothing. The domain chips already work
    // this way; the alternative is a control that is always there and never
    // does anything, on the catalog most people have.
    fetchCatalog.mockResolvedValueOnce({
      ...RESPONSE,
      sources: [{ key: "local", label: "Local", count: 2 }],
    });
    renderWithProviders(<CatalogPanel />);
    await screen.findByRole("button", { name: "Zillow One" });
    expect(screen.queryByRole("group", { name: /sources/i })).not.toBeInTheDocument();
  });

  it("marks the card of an item that arrived from a registered origin", async () => {
    renderWithProviders(<CatalogPanel />);
    const card = (await screen.findByRole("button", { name: "Flanged Shaft" })).closest(
      ".item-card",
    );
    expect(card).toHaveTextContent("Depot");
    // Where most items came from is not worth saying on every card.
    const local = screen.getByRole("button", { name: "Zillow One" }).closest(".item-card");
    expect(local).not.toHaveTextContent("Local");
  });

  it("spells an origin this file has never heard of, because the server does", async () => {
    // The point of the whole change, from the panel's side. Nothing here knows
    // what a `depot` is — the label comes from the registry along with the key,
    // so a build that ships another way of arriving is spelled correctly without
    // this tree being edited. A table in here would have rendered no chip at all.
    vi.mocked(api.fetchCatalogOrigins).mockResolvedValueOnce({
      origins: [
        { key: "local", label: "Local" },
        { key: "depot", label: "The Depot" },
      ],
    });
    fetchCatalog.mockResolvedValueOnce({
      ...RESPONSE,
      items: [item({ house_id: "d-1", name: "From A Depot", project_id: 41, source: "depot" })],
      total: 1,
      sources: [{ key: "depot", label: "The Depot", count: 1 }],
    });
    renderWithProviders(<CatalogPanel />);
    const card = (await screen.findByRole("button", { name: "From A Depot" })).closest(
      ".item-card",
    );
    expect(card).toHaveTextContent("The Depot");
  });

  it("says nothing rather than guessing when the origins could not be read", async () => {
    // A card missing one line is a card missing one line. A card labelled from
    // a table this file kept for the purpose would be a card that is wrong the
    // first time a build adds an arrival.
    vi.mocked(api.fetchCatalogOrigins).mockRejectedValueOnce(new Error("offline"));
    renderWithProviders(<CatalogPanel />);
    const card = (await screen.findByRole("button", { name: "Flanged Shaft" })).closest(
      ".item-card",
    );
    expect(card).not.toHaveTextContent("Depot");
    // And the listing itself is unaffected — this failure is not that one.
    expect(screen.getByRole("button", { name: "Zillow One" })).toBeInTheDocument();
  });

  it("opens a project when a card is clicked", async () => {
    const { store } = renderWithProviders(<CatalogPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Zillow One" }));
    expect(store.get().activeProjectId).toBe(11);
  });

  it("customizes an item: clones it into an editable copy and opens it (#22)", async () => {
    const { store } = renderWithProviders(<CatalogPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Customize Zillow One" }));
    await waitFor(() =>
      expect(api.cloneProject).toHaveBeenCalledWith(11, "Zillow One (copy)"),
    );
    await waitFor(() => expect(store.get().activeProjectId).toBe(999));
  });

  it("pages with Load more, appending the next page", async () => {
    fetchCatalog.mockResolvedValueOnce({ ...RESPONSE, items: [ITEMS[0]], total: 3 });
    fetchCatalog.mockResolvedValueOnce({
      ...RESPONSE, items: [ITEMS[1]], total: 3, offset: 1,
    });
    renderWithProviders(<CatalogPanel />);
    await screen.findByRole("button", { name: "Zillow One" });
    expect(screen.getByText(/1 of 3/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() =>
      expect(fetchCatalog).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 })),
    );
    // both pages are now shown together
    expect(await screen.findByRole("button", { name: "Flanged Shaft" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zillow One" })).toBeInTheDocument();
  });

  it("offers Remove only on an item that arrived here", async () => {
    // A bundled item ships with the app on a read-only mount — the server would
    // refuse it, so the control is not offered in the first place.
    fetchCatalog.mockResolvedValue({ ...RESPONSE, items: [ITEMS[0], RECEIVED] });
    renderWithProviders(<CatalogPanel />);

    expect(await screen.findByRole("button", { name: "Remove Received One" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Zillow One" })).toBeNull();
  });

  it("offers Remove on a record whose files are gone, and says what it takes", async () => {
    // The one removal the app had no way to reach. Its files are already gone,
    // so promising to remove them would describe something that has happened —
    // and the record is what stands between its owner and receiving it again.
    fetchCatalog.mockResolvedValue({
      ...RESPONSE,
      items: [item({ house_id: "recv-1", name: "Received One", project_id: 31,
        removable: true, files_missing: true, source: null })],
      total: 1,
    });
    renderWithProviders(<CatalogPanel />);
    // Named for what it takes: "Remove" in front of an item whose files are
    // already gone describes the wrong act, and the label is the whole of what
    // a screen reader is given.
    const button = await screen.findByRole("button", {
      name: "Clear leftover record for Received One",
    });
    expect(screen.queryByRole("button", { name: "Remove Received One" })).toBeNull();
    fireEvent.click(button);

    expect(screen.getByRole("heading", { name: "Clear leftover record?" })).toBeInTheDocument();
    expect(screen.getByText(/no files left on this machine/)).toBeInTheDocument();
    expect(screen.queryByText(/and its files will be removed/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.removeCatalogItem).toHaveBeenCalledWith("recv-1"));
  });

  it("keeps the received-item wording for one whose files are still here", async () => {
    // The other side of the same branch: with both cases behind one button, a
    // collapsed ternary would tell somebody their files are about to go when
    // they are already gone, or the reverse.
    fetchCatalog.mockResolvedValue({ ...RESPONSE, items: [RECEIVED], total: 1 });
    renderWithProviders(<CatalogPanel />);
    const button = await screen.findByRole("button", { name: "Remove Received One" });
    fireEvent.click(button);

    expect(screen.getByRole("heading", { name: "Remove catalog item?" })).toBeInTheDocument();
    expect(screen.getByText(/and its files will be removed/)).toBeInTheDocument();
  });

  it("removes a received item once confirmed, then refetches the list", async () => {
    fetchCatalog.mockResolvedValue({ ...RESPONSE, items: [RECEIVED], total: 1 });
    renderWithProviders(<CatalogPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Received One" }));

    // Removal takes the files with it, so it asks first — as deleting a project does.
    expect(api.removeCatalogItem).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(api.removeCatalogItem).toHaveBeenCalledWith("recv-1"));
    await waitFor(() => expect(fetchCatalog).toHaveBeenCalledTimes(2));
  });

  it("stops showing the removed item — the selection and its transcript both go", async () => {
    // ChatPanel renders `messages` whether or not a project is selected, so
    // leaving them would show the removed item's chat under an empty workspace.
    fetchCatalog.mockResolvedValue({ ...RESPONSE, items: [RECEIVED], total: 1 });
    const { store } = renderWithProviders(<CatalogPanel />, {
      activeProjectId: 31,
      chatPending: "make it taller",
      messages: [
        {
          id: 1, seq: 1, role: "user", content: "make it taller", status: "ok",
          error: null, version_id: null, created_at: "", blocks: [],
        },
      ],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Remove Received One" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(store.get().activeProjectId).toBeNull());
    expect(store.get().messages).toEqual([]);
    expect(store.get().chatPending).toBeNull();
  });

  it("shows an empty state when a search matches nothing", async () => {
    renderWithProviders(<CatalogPanel />);
    await screen.findByRole("button", { name: "Zillow One" });
    fetchCatalog.mockResolvedValue({ ...RESPONSE, items: [], total: 0, categories: [] });
    fireEvent.change(screen.getByRole("searchbox", { name: "Search catalog" }), {
      target: { value: "no such thing" },
    });
    expect(await screen.findByText(/No catalog items match/)).toBeInTheDocument();
  });
});
