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
