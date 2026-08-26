import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { SettingsStatus } from "../api";
import { renderWithProviders } from "../test/utils";
import { PrinterSettings } from "./PrinterSettings";

vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  forgetPrinterProfile: vi.fn(),
}));

/** A load that reports nothing saved about the printer.
 *
 * The state every existing installation is in: no address entered, and the
 * engine keeping its own default for each measurement.
 */
const STATUS: SettingsStatus = {
  providers: ["bedrock", "anthropic", "openai"],
  provider: "bedrock",
  provider_source: "default",
  orchestrator_model: "claude-opus",
  orchestrator_model_source: "default",
  codegen_model: "claude-sonnet",
  codegen_model_source: "default",
  aws_region: "us-east-1",
  aws_region_source: "default",
  printer_address: null,
  printer_bed_width: null,
  printer_bed_depth: null,
  printer_max_height: null,
  printer_nozzle_diameter: null,
  printer_filament_diameter: null,
  printer_nozzle_temperature: null,
  printer_bed_temperature: null,
  secrets: {},
};

afterEach(() => vi.clearAllMocks());

describe("PrinterSettings", () => {
  describe("the printer profile", () => {
    it("shows a saved measurement", async () => {
      vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, printer_bed_width: 300 });
      renderWithProviders(<PrinterSettings />);
      const input = (await screen.findByLabelText("Bed width (mm)")) as HTMLInputElement;
      expect(input.value).toBe("300");
    });

    it("is blank, not the default's value, when nothing is saved", async () => {
      // The placeholder shows what the engine would use. Filling the box with
      // it would make "I have not said" indistinguishable from "I chose exactly
      // the default", and the two behave differently on the next upgrade.
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      const input = (await screen.findByLabelText("Bed width (mm)")) as HTMLInputElement;
      expect(input.value).toBe("");
    });

    it("sends a typed measurement as a number", async () => {
      // A number, not the text that was typed: the endpoint's model is typed,
      // and "300" would be stored as text for `slicing` to coerce later.
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      fireEvent.change(await screen.findByLabelText("Bed width (mm)"), {
        target: { value: "300" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
      expect(vi.mocked(api.saveSettings).mock.calls[0][0].printer_bed_width).toBe(300);
    });

    it("leaves the fields it was not given alone", async () => {
      // Same rule as the address: this endpoint only ever sets, so a blank box
      // means "keep what is there" rather than "make it zero". Saved alongside
      // a field that *was* filled in, which is also what shows the seven are
      // independent of each other rather than sent as a block.
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      fireEvent.change(await screen.findByLabelText("Bed depth (mm)"), {
        target: { value: "250" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());

      const patch = vi.mocked(api.saveSettings).mock.calls[0][0];
      expect(patch.printer_bed_depth).toBe(250);
      expect(patch).not.toHaveProperty("printer_bed_width");
      expect(patch).not.toHaveProperty("printer_nozzle_diameter");
    });

    it("does not send something that is not a number", async () => {
      // The server refuses it either way; sending it would produce a refusal
      // naming a field, over a box the reader can see is wrong themselves.
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      fireEvent.change(await screen.findByLabelText("Bed width (mm)"), {
        target: { value: "wide" },
      });
      fireEvent.change(await screen.findByLabelText("Bed depth (mm)"), {
        target: { value: "250" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());

      const patch = vi.mocked(api.saveSettings).mock.calls[0][0];
      expect(patch).not.toHaveProperty("printer_bed_width");
      // The good field still goes: one unusable box does not cost the save.
      expect(patch.printer_bed_depth).toBe(250);
    });

    it("says which boxes did not take, rather than a green save", async () => {
      // Every box that reaches the drop was typed into by hand -- a seeded value
      // is always a finite number rendered back as text -- so a silent drop
      // under "Settings saved" tells somebody their measurement took when it
      // did not.
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      fireEvent.change(await screen.findByLabelText("Bed width (mm)"), {
        target: { value: "wide" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(screen.getByText("Some measurements were not saved")).toBeInTheDocument(),
      );
      // The toast body, not the field's own label -- both carry the name, and
      // only one of them is the thing under test.
      expect(
        screen.getByText("Bed width (mm) — each needs a number."),
      ).toBeInTheDocument();
      expect(screen.queryByText("Printer saved")).not.toBeInTheDocument();
    });

    it("forgets every measurement, which saving cannot do", async () => {
      // A blank box means "leave it alone", so without this a mistyped bed is
      // permanent short of editing settings.json by hand.
      vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, printer_bed_width: 300 });
      vi.mocked(api.forgetPrinterProfile).mockResolvedValue({ ok: true });
      renderWithProviders(<PrinterSettings />);
      const input = (await screen.findByLabelText("Bed width (mm)")) as HTMLInputElement;
      expect(input.value).toBe("300");

      fireEvent.click(screen.getByRole("button", { name: "Forget measurements" }));

      await waitFor(() => expect(api.forgetPrinterProfile).toHaveBeenCalled());
      await waitFor(() => expect(input.value).toBe(""));
    });

    it("offers every measurement the slicer is given", async () => {
      // The set is the contract: a field the panel cannot set is a default
      // nobody can correct.
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      for (const label of [
        "Bed width (mm)",
        "Bed depth (mm)",
        "Maximum height (mm)",
        "Nozzle (mm)",
        "Filament (mm)",
        "Nozzle temperature (°C)",
        "Bed temperature (°C)",
      ]) {
        expect(await screen.findByLabelText(label)).toBeInTheDocument();
      }
    });
  });

  describe("the printer address", () => {
    it("shows the saved address", async () => {
      vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, printer_address: "192.168.1.7" });
      renderWithProviders(<PrinterSettings />);
      const input = (await screen.findByLabelText("3D printer address")) as HTMLInputElement;
      expect(input.value).toBe("192.168.1.7");
    });

    it("is blank, not the string null, when none is set", async () => {
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      const input = (await screen.findByLabelText("3D printer address")) as HTMLInputElement;
      expect(input.value).toBe("");
    });

    it("saves an entered address", async () => {
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      const input = await screen.findByLabelText("3D printer address");
      fireEvent.change(input, { target: { value: " 192.168.1.9 " } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
      expect(vi.mocked(api.saveSettings).mock.calls[0][0].printer_address).toBe("192.168.1.9");
    });

    it("leaves a saved address alone when the box is emptied", async () => {
      // The endpoint only ever sets, so sending "" would be a no-op that reads
      // to the user as "forgotten". Omitting it says the same thing honestly.
      vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, printer_address: "192.168.1.7" });
      vi.mocked(api.saveSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      const input = await screen.findByLabelText("3D printer address");
      fireEvent.change(input, { target: { value: "" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
      expect(vi.mocked(api.saveSettings).mock.calls[0][0]).not.toHaveProperty("printer_address");
    });

    it("tests the address in the box rather than the saved one", async () => {
      // Finding out an address is wrong should not require saving it first.
      vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, printer_address: "192.168.1.7" });
      const testPrinter = vi
        .spyOn(api, "testPrinter")
        .mockResolvedValue({ ok: true, detail: "open", reason: "", status: {}, status_detail: "" });
      renderWithProviders(<PrinterSettings />);
      const input = await screen.findByLabelText("3D printer address");
      fireEvent.change(input, { target: { value: "192.168.1.99" } });
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      await waitFor(() => expect(testPrinter).toHaveBeenCalledWith("192.168.1.99"));
      await waitFor(() => expect(screen.getByText("Printer answered")).toBeInTheDocument());
    });

    it("offers to forget only once there is something saved", async () => {
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      renderWithProviders(<PrinterSettings />);
      await screen.findByLabelText("3D printer address");
      expect(screen.queryByRole("button", { name: "Forget" })).not.toBeInTheDocument();
    });

    it("forgets a saved address", async () => {
      // Saving cannot do this — a blank box there means "leave it alone" — so
      // without its own control a mistyped address is permanent.
      vi.mocked(api.getSettings).mockResolvedValue({ ...STATUS, printer_address: "192.168.1.7" });
      const forget = vi.spyOn(api, "forgetPrinterAddress").mockResolvedValue({ ok: true });
      renderWithProviders(<PrinterSettings />);
      const input = (await screen.findByLabelText("3D printer address")) as HTMLInputElement;
      fireEvent.click(screen.getByRole("button", { name: "Forget" }));
      await waitFor(() => expect(forget).toHaveBeenCalled());
      await waitFor(() => expect(input.value).toBe(""));
      expect(screen.queryByRole("button", { name: "Forget" })).not.toBeInTheDocument();
    });

    it("says what the printer is doing, not just that it answered", async () => {
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.spyOn(api, "testPrinter").mockResolvedValue({
        ok: true,
        detail: "192.168.1.7:9100 is accepting connections",
        reason: "",
        status: { idle: true, printing: false },
        status_detail: "",
      });
      renderWithProviders(<PrinterSettings />);
      await screen.findByLabelText("3D printer address");
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      // An open port says the path is there; the device naming its own state is
      // what says the thing listening is a printer.
      await waitFor(() => expect(screen.getByText(/idle and ready/)).toBeInTheDocument());
    });

    it("reports a printer that did not answer", async () => {
      vi.mocked(api.getSettings).mockResolvedValue(STATUS);
      vi.spyOn(api, "testPrinter").mockResolvedValue({
        ok: false,
        detail: "refused: nobody home",
        reason: "refused",
        status: {},
        status_detail: "",
      });
      renderWithProviders(<PrinterSettings />);
      await screen.findByLabelText("3D printer address");
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      await waitFor(() =>
        expect(screen.getByText("No answer from the printer")).toBeInTheDocument(),
      );
    });
  });
});
