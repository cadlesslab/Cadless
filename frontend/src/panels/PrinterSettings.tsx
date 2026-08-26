/** Everything about the printer in the room: where it is, and what it is.
 *
 * It lives beside Export rather than in Settings because that is where somebody
 * is standing when they need it — the moment you want a physical object out of
 * this is the moment the address matters. Settings keeps the engine: provider,
 * models, keys, tuning.
 *
 * **Address and profile together, not split.** They are one object — "my
 * printer" — and separating "where to send" from "what it can hold" would put
 * half the answer in each of two panels. The build volume is what decides
 * whether a model can be printed at all, so it belongs next to the button that
 * prints it.
 *
 * It owns its own load and save rather than being handed them. The settings
 * endpoint only ever sets, so a patch of printer fields cannot disturb a
 * provider or a key it does not name, and a component that loads what it shows
 * cannot drift from a parent that stopped rendering it.
 */
import { useEffect, useState } from "react";

import * as api from "../api";
import type { SettingsStatus, SettingsUpdate } from "../api";
import { Button, HelpPopover, TextInput, useToast } from "../components";
import { errMessage } from "../errors";

/** The printer's own measurements, in the order somebody would read them off it.
 *
 * The build volume first, because it is the one that decides whether a model
 * can be printed at all -- the rest decides whether it comes out well. Every
 * field is optional: the engine keeps its own default for anything left blank,
 * so a panel nobody opens changes nothing about how a model is sliced.
 */
type PrinterField = {
  field:
    | "printer_bed_width"
    | "printer_bed_depth"
    | "printer_max_height"
    | "printer_nozzle_diameter"
    | "printer_filament_diameter"
    | "printer_nozzle_temperature"
    | "printer_bed_temperature";
  label: string;
  placeholder: string;
};

export const PRINTER_FIELDS: PrinterField[] = [
  { field: "printer_bed_width", label: "Bed width (mm)", placeholder: "210" },
  { field: "printer_bed_depth", label: "Bed depth (mm)", placeholder: "200" },
  { field: "printer_max_height", label: "Maximum height (mm)", placeholder: "195" },
  { field: "printer_nozzle_diameter", label: "Nozzle (mm)", placeholder: "0.4" },
  { field: "printer_filament_diameter", label: "Filament (mm)", placeholder: "1.75" },
  { field: "printer_nozzle_temperature", label: "Nozzle temperature (°C)", placeholder: "205" },
  { field: "printer_bed_temperature", label: "Bed temperature (°C)", placeholder: "60" },
];

/** A saved number as text for an input, or blank when nothing is saved.
 *
 * Blank rather than the default's value, so the placeholder can show what the
 * engine would use while the field itself stays empty -- which is what makes
 * "I have not said" distinguishable from "I chose exactly the default".
 */
const numberOrBlank = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? String(value) : "";

export function PrinterSettings() {
  const toast = useToast();
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [address, setAddress] = useState("");
  // Held as text, not numbers: an input mid-typing is "3", "30", "30." before
  // it is 300, and coercing on every keystroke fights the person typing.
  const [printer, setPrinter] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getSettings()
      .then((s) => {
        if (!alive) return;
        setStatus(s);
        setAddress(s.printer_address ?? "");
        setPrinter(
          Object.fromEntries(PRINTER_FIELDS.map(({ field }) => [field, numberOrBlank(s[field])])),
        );
      })
      .catch((err) => {
        if (!alive) return;
        toast.error("Could not load the printer settings", errMessage(err));
      });
    return () => {
      alive = false;
    };
  }, [toast]);

  async function onSave() {
    const patch: SettingsUpdate = {};
    if (address.trim()) patch.printer_address = address.trim();

    const unusable: string[] = [];
    for (const { field, label } of PRINTER_FIELDS) {
      const typed = (printer[field] ?? "").trim();
      // Blank means "leave it as it is": the endpoint only ever sets, and the
      // engine keeps its default for anything it is not told.
      if (!typed) continue;
      const value = Number(typed);
      if (Number.isFinite(value)) patch[field] = value;
      // Every box that reaches here was typed into by hand -- a seeded value is
      // always a finite number rendered back as text -- so dropping one silently
      // under a green "Saved" tells somebody their measurement took when it
      // did not.
      else unusable.push(label);
    }

    setSaving(true);
    try {
      setStatus(await api.saveSettings(patch));
      if (unusable.length) {
        toast.error(
          "Some measurements were not saved",
          `${unusable.join(", ")} — each needs a number.`,
        );
      } else {
        toast.success("Printer saved");
      }
    } catch (err) {
      toast.error("Could not save the printer settings", errMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function onTest() {
    setTesting(true);
    try {
      const result = await api.testPrinter(address.trim() || undefined);
      if (!result.ok) {
        toast.error("No answer from the printer", result.detail);
        return;
      }
      // An open port says the path is there; the device naming its own state
      // says the thing at the other end is a printer. Worth showing, since the
      // second is the half that distinguishes it from anything else listening.
      const state = result.status?.printing
        ? "It is printing something now."
        : result.status?.idle
          ? "It is idle and ready."
          : "";
      toast.success("Printer answered", [result.detail, state].filter(Boolean).join(" "));
    } catch (err) {
      toast.error("Could not test the printer", errMessage(err));
    } finally {
      setTesting(false);
    }
  }

  /** Remove the saved address. Saving cannot do this: a blank box there means
   * "leave it alone", which would make a typo permanent. */
  async function onForgetAddress() {
    try {
      await api.forgetPrinterAddress();
      setAddress("");
      setStatus((s) => (s ? { ...s, printer_address: null } : s));
      toast.success("Printer address forgotten");
    } catch (err) {
      toast.error("Could not forget the printer address", errMessage(err));
    }
  }

  /** Return every measurement to its default, for the same reason. */
  async function onForgetProfile() {
    try {
      await api.forgetPrinterProfile();
      setPrinter({});
      setStatus((s) =>
        s ? { ...s, ...Object.fromEntries(PRINTER_FIELDS.map(({ field }) => [field, null])) } : s,
      );
      toast.success("Printer profile forgotten", "Back to the defaults.");
    } catch (err) {
      toast.error("Could not forget the printer profile", errMessage(err));
    }
  }

  return (
    <div className="printer-settings">
      <div className="settings-head">
        <label htmlFor="printer-address">3D printer address</label>
        {/* A mark rather than a paragraph. The explanation is worth having and
            is not worth re-reading on every visit by everyone who set the
            address months ago. */}
        <HelpPopover label="About the printer address" title="3D printer address">
          The printer's address on your own network — Print sends jobs here. A public address is
          refused.
        </HelpPopover>
      </div>
      <TextInput
        id="printer-address"
        aria-label="3D printer address"
        placeholder="192.168.0.42"
        value={address}
        onChange={(e) => setAddress(e.target.value)}
      />

      {/* Collapsed, like Engine tuning: most people print on whatever the
          defaults describe. It is here rather than in Settings because the bed
          is what decides whether a model can be printed at all, which is a
          question asked at the moment of printing. */}
      <details className="settings-tuning">
        <summary>Printer profile</summary>
        <small className="settings-note">
          What your printer is, as against where it is. The build volume decides whether a model is
          refused as too big before slicing starts. A blank box means "leave this alone" rather than
          "use the default" — Forget is what returns a saved measurement to the default.
        </small>
        {PRINTER_FIELDS.map(({ field, label, placeholder }) => (
          <label className="settings-field" key={field}>
            <span>{label}</span>
            <TextInput
              aria-label={label}
              inputMode="decimal"
              placeholder={placeholder}
              value={printer[field] ?? ""}
              onChange={(e) => setPrinter((p) => ({ ...p, [field]: e.target.value }))}
            />
          </label>
        ))}
        <div className="export-actions">
          <Button type="button" size="sm" variant="ghost" onClick={onForgetProfile}>
            Forget measurements
          </Button>
        </div>
      </details>

      <div className="export-actions">
        <Button type="button" size="sm" variant="primary" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={testing} onClick={onTest}>
          {testing ? "Testing…" : "Test connection"}
        </Button>
        {status?.printer_address && (
          <Button type="button" size="sm" variant="ghost" onClick={onForgetAddress}>
            Forget
          </Button>
        )}
      </div>
    </div>
  );
}
