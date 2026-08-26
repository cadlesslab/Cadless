/** Settings flyout: pick the LLM provider, enter API keys, and set model overrides.
 * Values persist to runtime-db/settings.json via POST /settings and apply without a
 * restart.
 *
 * Keys are write-only: the server returns only whether each key is set (never its
 * value), so inputs start blank and a saved key shows a "set" hint. Switching to
 * OpenAI surfaces the model-repoint requirement (the Claude-slug defaults would
 * otherwise fail); switching to Anthropic notes that embeddings/RAG are skipped. */
import { useEffect, useState } from "react";

import * as api from "../api";
import type { SettingsStatus, SettingsUpdate } from "../api";
import { Button, Panel, TextInput, useToast } from "../components";
import { errMessage } from "../errors";

const PROVIDER_LABELS: Record<string, string> = {
  bedrock: "AWS Bedrock",
  anthropic: "Anthropic (Claude API key)",
  openai: "OpenAI (API key)",
};

// Per-provider model defaults. Switching provider realigns the model fields to the
// provider's family (OpenAI ids vs Claude slugs) so a save doesn't hit the
// provider/model validation. Only a mismatched field is overwritten, so a custom
// same-family model the user typed is preserved.
const MODEL_DEFAULTS: Record<string, { orchestrator: string; codegen: string }> = {
  openai: { orchestrator: "gpt-4o", codegen: "gpt-4o" },
  bedrock: { orchestrator: "opus-4-6", codegen: "sonnet-4-6" },
  anthropic: { orchestrator: "opus-4-6", codegen: "sonnet-4-6" },
};
const isOpenAiModel = (m: string) => /^(gpt|o\d|chatgpt)/i.test(m);

/** Engine tuning knobs, rendered from a table so adding one is a row.
 *
 * Only the knobs the server calls Tier A appear here. The ones that multiply
 * per-turn spend are refused unless the server was launched with the advanced
 * gate set, and operator-only configuration is not on the request model at all.
 */
type Knob = { field: string; label: string; kind: "number" | "toggle" | "text"; step?: string };

const TUNING_KNOBS: Knob[] = [
  { field: "rag_top_k", label: "Grounding examples", kind: "number", step: "1" },
  { field: "rag_similarity_floor", label: "Similarity floor", kind: "number", step: "0.05" },
  { field: "rag_success_weight", label: "Success weight", kind: "number", step: "0.05" },
  { field: "rag_require_tag_overlap", label: "Require tag overlap", kind: "toggle" },
  { field: "bedrock_temperature", label: "Codegen temperature", kind: "number", step: "0.1" },
  { field: "forge_temperature", label: "Candidate temperature", kind: "number", step: "0.1" },
  { field: "vlm_model_slug", label: "Vision model", kind: "text" },
  { field: "bedrock_model_slug", label: "Primary model", kind: "text" },
  { field: "bedrock_fast_model_slug", label: "Fast-path model", kind: "text" },
];

// The knob table is keyed by name, so reading a value out of the typed status
// needs one cast. Confined to these two helpers rather than spread through the
// component, and the names themselves are checked by `TuningKnobs` in api.ts.
const readKnob = (s: SettingsStatus, field: string) =>
  (s as unknown as Record<string, string | number | boolean>)[field];
const knobSource = (s: SettingsStatus | null, field: string) =>
  s ? (s as unknown as Record<string, string>)[`${field}_source`] : undefined;

export function SettingsPanel() {
  const toast = useToast();
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [provider, setProvider] = useState("anthropic");
  const [orchestratorModel, setOrchestratorModel] = useState("");
  const [codegenModel, setCodegenModel] = useState("");
  const [awsRegion, setAwsRegion] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [awsAccessKeyId, setAwsAccessKeyId] = useState("");
  const [awsSecretAccessKey, setAwsSecretAccessKey] = useState("");
  // Edit state for the knob table. Numbers and text are held as strings so a
  // half-typed "0." is not coerced away under the cursor; the patch converts.
  const [knobs, setKnobs] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);
  // Distinct from `!status`, which is also true while the first load is still in
  // flight — claiming the load failed then would be a lie on the happy path.
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getSettings()
      .then((s) => {
        if (!alive) return;
        setStatus(s);
        setProvider(s.provider);
        setOrchestratorModel(s.orchestrator_model);
        setCodegenModel(s.codegen_model);
        setAwsRegion(s.aws_region);
        setKnobs(
          Object.fromEntries(
            TUNING_KNOBS.map((k) => {
              // A status without knobs (an older server, or a narrow test
              // fixture) seeds a blank rather than the string "undefined".
              const v = readKnob(s, k.field);
              if (k.kind === "toggle") return [k.field, Boolean(v)];
              return [k.field, v === undefined || v === null ? "" : String(v)];
            }),
          ),
        );
      })
      .catch((err) => {
        if (!alive) return;
        setLoadFailed(true);
        toast.error("Could not load settings", errMessage(err));
      });
    return () => {
      alive = false;
    };
  }, [toast]);

  async function onSave() {
    // Every field is seeded by a successful load. Without one they still hold
    // their placeholder values, so saving would write a provider and models the
    // user never chose over whatever is actually configured.
    if (!status) return;
    const patch: SettingsUpdate = {
      provider,
      orchestrator_model: orchestratorModel,
      codegen_model: codegenModel,
    };
    if (provider === "bedrock") patch.aws_region = awsRegion;
    if (anthropicKey) patch.anthropic_api_key = anthropicKey;
    if (openaiKey) patch.openai_api_key = openaiKey;
    if (awsAccessKeyId) patch.aws_access_key_id = awsAccessKeyId;
    if (awsSecretAccessKey) patch.aws_secret_access_key = awsSecretAccessKey;

    // Only knobs the user actually moved. Sending the whole table would flip
    // every untouched knob's provenance from "default" to "saved", which reads
    // as a choice nobody made. This also covers the environment-pinned ones:
    // their control is disabled, so their value never diverges from the status
    // and they are never in the patch.
    const knobPatch = patch as Record<string, unknown>;
    for (const k of TUNING_KNOBS) {
      const current = readKnob(status, k.field);
      // Nothing to diff against: a server that did not report this knob gives no
      // baseline, and a toggle seeded false would otherwise read as "changed"
      // against an absent value and be saved without anyone touching it.
      if (current === undefined) continue;
      const raw = knobs[k.field];
      if (raw === undefined) continue;
      // An emptied box means "leave this alone", not zero. Number("") is 0 and
      // passes a finite check, so without this, clearing the field to retype it
      // and hitting Save would quietly store 0 — which for rag_top_k is the
      // documented "no retrieval at all" setting.
      if (typeof raw === "string" && raw.trim() === "") continue;
      const next = k.kind === "number" ? Number(raw) : raw;
      if (k.kind === "number" && !Number.isFinite(next as number)) continue;
      if (next !== current) knobPatch[k.field] = next;
    }

    setSaving(true);
    try {
      const next = await api.saveSettings(patch);
      setStatus(next);
      setAnthropicKey("");
      setOpenaiKey("");
      setAwsAccessKeyId("");
      setAwsSecretAccessKey("");
      toast.success("Settings saved", "Applied without a restart.");
    } catch (err) {
      toast.error("Could not save settings", errMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const providers = status?.providers ?? ["bedrock", "anthropic", "openai"];
  const keyHint = (field: string) => {
    const s = status?.secrets?.[field];
    return s?.set ? `Key set (${s.source}) — leave blank to keep` : undefined;
  };

  function onProviderChange(next: string) {
    setProvider(next);
    const d = MODEL_DEFAULTS[next] ?? MODEL_DEFAULTS.anthropic;
    if (isOpenAiModel(orchestratorModel) !== (next === "openai")) setOrchestratorModel(d.orchestrator);
    if (isOpenAiModel(codegenModel) !== (next === "openai")) setCodegenModel(d.codegen);
  }

  return (
    <Panel title="Settings">
      <div className="settings-form">
        <label className="settings-field">
          <span>Model provider</span>
          <select
            className="field"
            aria-label="Model provider"
            value={provider}
            onChange={(e) => onProviderChange(e.target.value)}
          >
            {providers.map((p) => (
              <option key={p} value={p}>
                {PROVIDER_LABELS[p] ?? p}
              </option>
            ))}
          </select>
        </label>

        {provider === "anthropic" && (
          <label className="settings-field">
            <span>Anthropic API key</span>
            <TextInput
              type="password"
              autoComplete="off"
              placeholder={keyHint("anthropic_api_key") ?? "sk-ant-..."}
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
            />
            <small>
              Anthropic has no embeddings API — RAG/KB retrieval is skipped automatically.
            </small>
          </label>
        )}

        {provider === "openai" && (
          <label className="settings-field">
            <span>OpenAI API key</span>
            <TextInput
              type="password"
              autoComplete="off"
              placeholder={keyHint("openai_api_key") ?? "sk-..."}
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
            />
          </label>
        )}

        {provider === "bedrock" && (
          <>
            <label className="settings-field">
              <span>AWS access key ID</span>
              <TextInput
                type="password"
                autoComplete="off"
                placeholder={keyHint("aws_access_key_id") ?? "AKIA..."}
                value={awsAccessKeyId}
                onChange={(e) => setAwsAccessKeyId(e.target.value)}
              />
            </label>
            <label className="settings-field">
              <span>AWS secret access key</span>
              <TextInput
                type="password"
                autoComplete="off"
                placeholder={keyHint("aws_secret_access_key") ?? "AWS secret access key"}
                value={awsSecretAccessKey}
                onChange={(e) => setAwsSecretAccessKey(e.target.value)}
              />
            </label>
            <label className="settings-field">
              <span>AWS region</span>
              <TextInput
                placeholder="us-east-1"
                value={awsRegion}
                onChange={(e) => setAwsRegion(e.target.value)}
              />
              <small>
                Leave the keys blank to use AWS credentials from the environment or an
                instance role.
              </small>
            </label>
          </>
        )}

        <label className="settings-field">
          <span>Orchestrator model</span>
          <TextInput
            value={orchestratorModel}
            onChange={(e) => setOrchestratorModel(e.target.value)}
          />
        </label>
        <label className="settings-field">
          <span>Codegen model</span>
          <TextInput value={codegenModel} onChange={(e) => setCodegenModel(e.target.value)} />
        </label>

        {provider === "openai" && (
          <small className="settings-note">
            OpenAI needs OpenAI model ids — set the models above to e.g. <code>gpt-4o</code>, not the
            Claude-slug defaults.
          </small>
        )}

        {/* Collapsed by default: these change how generation behaves, and the
            panel's common errand is picking a provider or pasting a key. */}
        <details className="settings-tuning">
          <summary>Engine tuning</summary>
          {TUNING_KNOBS.map((k) => {
            const pinned = knobSource(status, k.field) === "env";
            const value = knobs[k.field];
            return (
              <label className="settings-field" key={k.field}>
                <span>{k.label}</span>
                {k.kind === "toggle" ? (
                  <input
                    type="checkbox"
                    aria-label={k.label}
                    checked={value === true}
                    disabled={pinned}
                    onChange={(e) => setKnobs((p) => ({ ...p, [k.field]: e.target.checked }))}
                  />
                ) : (
                  <TextInput
                    aria-label={k.label}
                    type={k.kind === "number" ? "number" : "text"}
                    step={k.step}
                    value={typeof value === "string" ? value : ""}
                    disabled={pinned}
                    onChange={(e) => setKnobs((p) => ({ ...p, [k.field]: e.target.value }))}
                  />
                )}
                {/* Saying so beats a control that silently ignores what is typed
                    into it: env beats the saved file, and only a restart moves it. */}
                {pinned && (
                  <small>Pinned by the environment — change it where the server is launched.</small>
                )}
              </label>
            );
          })}
        </details>

        {/* No role="status": the failure toast already announces this, and a live
            region mounted with its text already in place is unreliably announced
            anyway. The note exists to be read in place and to describe the button. */}
        {loadFailed && (
          <small className="settings-note" id="settings-load-failed">
            Current settings could not be loaded, so saving is disabled — the fields above
            are placeholders, not what is configured. Close and reopen this panel to retry
            once the backend is reachable.
          </small>
        )}
        <Button
          variant="primary"
          onClick={() => void onSave()}
          disabled={saving || !status}
          aria-describedby={loadFailed ? "settings-load-failed" : undefined}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </Panel>
  );
}
