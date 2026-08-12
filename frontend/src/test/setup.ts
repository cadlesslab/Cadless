import "@testing-library/jest-dom/vitest";

// jsdom lacks ResizeObserver, which Radix Slider (and others) rely on.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in globalThis)) {
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
}

// jsdom lacks matchMedia, which `useNarrow` reads to decide the layout. The
// default answers "not narrow", so every test that does not care about width
// gets the desktop layout — the one they were all written against. A test that
// does care replaces this wholesale rather than tweaking it.
if (!("matchMedia" in globalThis)) {
  (globalThis as { matchMedia?: unknown }).matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}
