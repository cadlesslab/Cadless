import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { SettingsStatus } from "../api";
import { renderWithProviders } from "../test/utils";
import { SettingsPanel } from "./SettingsPanel";

const STATUS: SettingsStatus = {
  providers: ["bedrock", "anthropic", "openai"],
  provider: "bedrock",
  provider_source: "default",
  orchestrator_model: "opus-4-6",
  orchestrator_model_source: "default",
  codegen_model: "sonnet-4-6",
  codegen_model_source: "default",
  aws_region: "us-east-1",
  aws_region_source: "default",
  secrets: {
    anthropic_api_key: { set: false, source: "unset" },
    openai_api_key: { set: false, source: "unset" },
    aws_access_key_id: { set: false, source: "unset" },
    aws_secret_access_key: { set: false, source: "unset" },
    aws_session_token: { set: false, source: "unset" },
  },
};

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
}));

afterEach(() => vi.clearAllMocks());

describe("SettingsPanel", () => {
  it("loads current settings into the provider selector", async () => {
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    const select = (await screen.findByLabelText("Model provider")) as HTMLSelectElement;
    expect(select.value).toBe("bedrock");
  });

  it("falls back to anthropic when settings fail to load", async () => {
    vi.mocked(api.getSettings).mockRejectedValue(new Error("boom"));
    renderWithProviders(<SettingsPanel />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    const select = (await screen.findByLabelText("Model provider")) as HTMLSelectElement;
    expect(select.value).toBe("anthropic");
  });

  it("masks a saved key: input stays empty, is a password, and shows a 'set' hint", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({
      ...STATUS,
      provider: "anthropic",
      secrets: { ...STATUS.secrets, anthropic_api_key: { set: true, source: "saved" } },
    });
    renderWithProviders(<SettingsPanel />);
    const keyInput = (await screen.findByPlaceholderText(/key set/i)) as HTMLInputElement;
    expect(keyInput.value).toBe(""); // the secret value is never pre-filled
    expect(keyInput.type).toBe("password");
  });

  it("saves the entered provider + key and clears the key input on success", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, provider: "anthropic" });
    vi.mocked(api.saveSettings).mockResolvedValue({
      ...STATUS,
      provider: "anthropic",
      secrets: { ...STATUS.secrets, anthropic_api_key: { set: true, source: "saved" } },
    });
    renderWithProviders(<SettingsPanel />);
    const keyInput = (await screen.findByPlaceholderText(/sk-ant|key set/i)) as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: "sk-ant-typed" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "anthropic", anthropic_api_key: "sk-ant-typed" }),
      ),
    );
    await waitFor(() => expect(keyInput.value).toBe(""));
  });

  it("surfaces the OpenAI model-repoint requirement when openai is picked", async () => {
    vi.mocked(api.getSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    const select = await screen.findByLabelText("Model provider");
    fireEvent.change(select, { target: { value: "openai" } });
    expect(screen.getByText(/gpt-4o/i)).toBeInTheDocument();
  });

  it("lets the user enter AWS credentials when Bedrock is selected", async () => {
    vi.mocked(api.getSettings).mockResolvedValue(STATUS); // provider is "bedrock"
    vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    const keyInput = (await screen.findByPlaceholderText(/AKIA/i)) as HTMLInputElement;
    expect(keyInput.type).toBe("password");
    fireEvent.change(keyInput, { target: { value: "AKIA-typed" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "bedrock", aws_access_key_id: "AKIA-typed" }),
      ),
    );
  });

  it("loads once and does not retry in a loop when the settings load fails", async () => {
    // The mock succeeds from the fourth call on, which bounds a regression. An
    // always-rejecting one does not fail against a looping panel, it wedges this
    // file — measured past 90s with no output — so the cap is what lets the
    // assertion below do the reporting.
    let calls = 0;
    vi.mocked(api.getSettings).mockImplementation(() => {
      calls += 1;
      return calls > 3
        ? Promise.resolve(STATUS)
        : Promise.reject(new Error("Failed to fetch"));
    });
    renderWithProviders(<SettingsPanel />);
    await screen.findByText(/could not load settings/i);
    expect(api.getSettings).toHaveBeenCalledTimes(1);
  });

  it("does not save a provider the user never chose when the load failed", async () => {
    // Capped like the test above and for the same measured reason.
    let calls = 0;
    vi.mocked(api.getSettings).mockImplementation(() => {
      calls += 1;
      return calls > 3
        ? Promise.resolve(STATUS)
        : Promise.reject(new Error("Failed to fetch"));
    });
    vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);

    // Synchronise on the note, not on the toast: the toast says "Could not load
    // settings" whatever the panel does, so waiting on it would let the note be
    // deleted without a test noticing.
    await screen.findByText(/fields above\s+are placeholders/i);
    const save = screen.getByRole("button", { name: /save/i });
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("aria-describedby", "settings-load-failed");

    // onSave reaches saveSettings before its first await, so a guard failure would
    // already be visible synchronously here — no sleep needed. Note this pins the
    // contract, not the early return in onSave: React keeps its own record of the
    // disabled prop, so neither removeAttribute("disabled") nor setting
    // .disabled = false makes the click dispatch (both measured at zero calls).
    // That guard only becomes reachable if the button ever goes aria-disabled.
    fireEvent.click(save);
    expect(api.saveSettings).not.toHaveBeenCalled();
  });

  it("switches the model fields to GPT defaults when OpenAI is picked", async () => {
    vi.mocked(api.getSettings).mockResolvedValue(STATUS); // bedrock: opus-4-6 / sonnet-4-6
    renderWithProviders(<SettingsPanel />);
    const select = await screen.findByLabelText("Model provider");
    await screen.findByDisplayValue("opus-4-6"); // the loaded Claude-slug default
    fireEvent.change(select, { target: { value: "openai" } });
    await waitFor(() => expect(screen.getAllByDisplayValue("gpt-4o")).toHaveLength(2));
  });

  it("sends only the knobs the user moved, so provenance stays honest", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({
      ...STATUS,
      rag_top_k: 3,
      rag_top_k_source: "default",
      rag_similarity_floor: 0.55,
      rag_similarity_floor_source: "default",
    });
    vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.change(await screen.findByLabelText("Grounding examples"), {
      target: { value: "8" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    const patch = vi.mocked(api.saveSettings).mock.calls[0][0];
    // Sent as a number, not the string the input held.
    expect(patch.rag_top_k).toBe(8);
    // Asserted as an exact set, not as one absence: checking a single untouched
    // knob would pass while the other eight rode along, and each one riding
    // along flips its source from "default" to "saved".
    const knobKeys = Object.keys(patch).filter(
      (k) => k.startsWith("rag_") || k.startsWith("bedrock_") || k.startsWith("forge_") || k.startsWith("vlm_"),
    );
    expect(knobKeys).toEqual(["rag_top_k"]);
  });

  it("leaves a knob alone when its field is cleared, rather than saving 0", async () => {
    // Number("") is 0 and passes a finite check, so an emptied box would store
    // 0 — which for rag_top_k is the "no retrieval at all" setting.
    vi.mocked(api.getSettings).mockResolvedValue({
      ...STATUS,
      rag_top_k: 3,
      rag_top_k_source: "default",
    });
    vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.change(await screen.findByLabelText("Grounding examples"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    expect(vi.mocked(api.saveSettings).mock.calls[0][0]).not.toHaveProperty("rag_top_k");
  });

  it("sends a knob turned off, rather than treating false as unset", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({
      ...STATUS,
      rag_require_tag_overlap: true,
      rag_require_tag_overlap_source: "saved",
    });
    vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(await screen.findByLabelText("Require tag overlap"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    expect(vi.mocked(api.saveSettings).mock.calls[0][0].rag_require_tag_overlap).toBe(false);
  });

  it("disables an environment-pinned knob and says why", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({
      ...STATUS,
      rag_top_k: 9,
      rag_top_k_source: "env",
    });
    vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
    renderWithProviders(<SettingsPanel />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    const input = (await screen.findByLabelText("Grounding examples")) as HTMLInputElement;
    // Accepting input the server would ignore is worse than refusing it.
    expect(input.disabled).toBe(true);
    expect(screen.getByText(/Pinned by the environment/)).toBeTruthy();

    // Follows from the control being disabled rather than from a second guard:
    // the value cannot diverge from the status, so the changed-only patch omits
    // it. Asserted to pin the outcome, not to claim a separate mechanism.
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    expect(vi.mocked(api.saveSettings).mock.calls[0][0]).not.toHaveProperty("rag_top_k");
  });
});
