"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Dialog from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { t } from "@/app/strings";

// ── API types (mirrors backend/api/settings.py) ─────────────────────────────

type CloudProvider = {
  id: string;
  label: string;
  base_url: string;
  api_key: string; // always "" from the server; user may type a new one
  has_key?: boolean;
  key_hint?: string;
  models: string[];
  enabled: boolean;
};

type RoleAssignment = { provider: string; model: string } | null;

type Settings = {
  version: number;
  ollama: { base_url: string };
  local_runtime: {
    kind: "ollama" | "openai_compatible";
    base_url: string;
    api_key: string;
    has_key?: boolean;
    key_hint?: string;
    allow_private_network: boolean;
    chat_model: string;
    vision_model: string;
    embedding_model: string;
    context_overrides: Record<string, number>;
    capabilities: Record<"tools" | "vision" | "embeddings" | "structured_output", "supported" | "unsupported" | "unknown">;
  };
  cloud_providers: CloudProvider[];
  roles: Record<string, RoleAssignment>;
};

type RoleInfo = {
  id: string;
  label: string;
  kind: "agent" | "pipeline";
  description: string;
  local_only?: boolean;
};

type OllamaModelInfo = {
  id: string;
  label: string;
  hint: string;
  in_registry: boolean;
};

type Availability = {
  ollama: { ok: boolean; base_url: string; detail: string; models: OllamaModelInfo[] };
  local_runtime?: { ok: boolean; kind: string; base_url: string; detail: string; models: OllamaModelInfo[] };
  cloud: { id: string; label: string; enabled: boolean; has_key: boolean; models: string[] }[];
};

// Known OpenAI-compatible endpoints, offered as presets when adding a provider.
const PROVIDER_PRESETS = [
  { id: "openai", label: "OpenAI", base_url: "https://api.openai.com/v1", models: ["gpt-4o-mini"] },
  { id: "openrouter", label: "OpenRouter", base_url: "https://openrouter.ai/api/v1", models: [] as string[] },
  { id: "groq", label: "Groq", base_url: "https://api.groq.com/openai/v1", models: [] as string[] },
  { id: "mistral", label: "Mistral", base_url: "https://api.mistral.ai/v1", models: [] as string[] },
  { id: "custom", label: t.settings.customProvider, base_url: "", models: [] as string[] },
];

function apiBase(): string {
  if (typeof window === "undefined") return "http://localhost:8000";
  const { protocol, hostname, host, port } = window.location;
  if (port === "3000") return `http://${hostname}:8000`;
  return `${protocol}//${host}`;
}

function authHeaders(): Record<string, string> {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("pilot_token") || "";
  return token ? { "X-Pilot-Token": token } : {};
}

async function apiFetch(path: string, init?: RequestInit) {
  const resp = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers || {}),
    },
  });
  const data = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, data };
}

// Encode a role assignment as one <select> value.
const INHERIT = "__inherit__";
const encodeAssignment = (a: RoleAssignment) => (a ? `${a.provider} ${a.model}` : INHERIT);
const decodeAssignment = (v: string): RoleAssignment => {
  if (v === INHERIT) return null;
  const idx = v.indexOf(" ");
  if (idx <= 0) return null;
  const provider = v.slice(0, idx);
  const model = v.slice(idx + 1); // model names may themselves contain spaces
  return provider && model ? { provider, model } : null;
};

export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [roleCatalog, setRoleCatalog] = useState<RoleInfo[]>([]);
  const [roleEnvDefaults, setRoleEnvDefaults] = useState<Record<string, string>>({});
  const [available, setAvailable] = useState<Availability | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [testStatus, setTestStatus] = useState<Record<string, string>>({});
  const [newPreset, setNewPreset] = useState("openai");

  const load = useCallback(async () => {
    setLoading(true);
    const [conf, avail] = await Promise.all([
      apiFetch("/api/settings/models"),
      apiFetch("/api/models/available"),
    ]);
    if (conf.ok) {
      setSettings(conf.data.settings);
      setRoleCatalog(conf.data.role_catalog ?? []);
      setRoleEnvDefaults(conf.data.role_env_defaults ?? {});
    } else {
      setErrors([conf.status === 401 ? t.settings.unauthorized : t.settings.loadFailed]);
    }
    if (avail.ok) setAvailable(avail.data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const update = useCallback((mutate: (draft: Settings) => void) => {
    setSettings((prev) => {
      if (!prev) return prev;
      const draft: Settings = JSON.parse(JSON.stringify(prev));
      mutate(draft);
      return draft;
    });
    setDirty(true);
  }, []);

  const save = useCallback(async () => {
    if (!settings) return;
    setSaving(true);
    const resp = await apiFetch("/api/settings/models", {
      method: "PUT",
      body: JSON.stringify(settings),
    });
    setSaving(false);
    const errs: string[] = resp.data?.errors ?? [];
    setErrors(errs);
    if (resp.ok && errs.length === 0) {
      setSettings(resp.data.settings);
      setDirty(false);
      toast.show(t.settings.saved, { kind: "info" });
    }
  }, [settings, toast]);

  const testProvider = useCallback(
    async (providerId: string, baseUrl: string, apiKey?: string, localOptions?: { kind: string; allow_private_network: boolean }) => {
      setTestStatus((s) => ({ ...s, [providerId]: t.settings.testing }));
      const resp = await apiFetch("/api/settings/test-provider", {
        method: "POST",
        body: JSON.stringify({ provider: providerId, base_url: baseUrl, api_key: apiKey || "", ...localOptions }),
      });
      const detail = resp.data?.detail ?? t.settings.testFailed;
      setTestStatus((s) => ({ ...s, [providerId]: `${resp.data?.ok ? "✓" : "✗"} ${detail}` }));
    },
    []
  );

  const addProvider = useCallback(() => {
    const preset = PROVIDER_PRESETS.find((p) => p.id === newPreset) ?? PROVIDER_PRESETS[0];
    update((draft) => {
      let id = preset.id === "custom" ? "provider" : preset.id;
      let n = 2;
      while (draft.cloud_providers.some((p) => p.id === id)) id = `${preset.id === "custom" ? "provider" : preset.id}${n++}`;
      draft.cloud_providers.push({
        id,
        label: preset.label,
        base_url: preset.base_url,
        api_key: "",
        models: [...preset.models],
        enabled: true,
      });
    });
  }, [newPreset, update]);

  const localModels = available?.local_runtime?.models ?? available?.ollama.models ?? [];

  // Options a role can be assigned to: every installed local model + every
  // model declared on a cloud provider (in the CURRENT edited state, so a
  // just-added model is selectable before saving).
  const roleOptions = useMemo(() => {
    const cloud = (settings?.cloud_providers ?? [])
      .filter((p) => p.enabled)
      .map((p) => ({
        provider: p.id,
        label: p.label,
        models: p.models,
      }));
    return { cloud };
  }, [settings]);

  const inheritLabel = (role: RoleInfo): string => {
    if (role.id === "default_agent") {
      const env = roleEnvDefaults["default_agent"];
      return `${t.settings.inheritEnv}${env ? ` (${env})` : ""}`;
    }
    const def = settings?.roles?.["default_agent"];
    if (def) return `${t.settings.inheritDefault} (${def.model})`;
    const env = roleEnvDefaults[role.id];
    return `${t.settings.inheritEnv}${env ? ` (${env})` : ""}`;
  };

  return (
    <Dialog icon="⚙" title={t.settings.title} className="settings" onClose={onClose}>
      <div className="mb">
        {loading ? (
          <p className="settings-note">{t.settings.loading}</p>
        ) : !settings ? (
          <p className="form-error" role="alert">{errors.join("; ") || t.settings.loadFailed}</p>
        ) : (
          <>
            <p className="settings-note">{t.settings.intro}</p>

            {/* ── Roles ─────────────────────────────────────────────── */}
            <section className="control-card settings-block">
              <div className="control-head">
                <span className="seclabel">{t.settings.rolesTitle}</span>
              </div>
              <p className="settings-note">{t.settings.rolesHint}</p>
              {(["agent", "pipeline"] as const).map((kind) => (
                <div key={kind}>
                  <div className="settings-subhead">
                    {kind === "agent" ? t.settings.agentRoles : t.settings.pipelineRoles}
                  </div>
                  {roleCatalog
                    .filter((r) => r.kind === kind)
                    .map((role) => (
                      <div key={role.id} className="settings-rolerow">
                        <div className="settings-rolemeta">
                          <div className="mt">{role.label}</div>
                          <div className="ms">{role.description}</div>
                        </div>
                        <select
                          className="fld"
                          aria-label={role.label}
                          value={encodeAssignment(settings.roles[role.id] ?? null)}
                          onChange={(e) =>
                            update((draft) => {
                              const parsed = decodeAssignment(e.target.value);
                              if (parsed) draft.roles[role.id] = parsed;
                              else delete draft.roles[role.id];
                            })
                          }
                        >
                          <option value={INHERIT}>{inheritLabel(role)}</option>
                          <optgroup label={t.settings.localGroup}>
                            {localModels.map((m) => (
                              <option key={m.id} value={`ollama ${m.id}`} title={m.hint}>
                                {m.label}{m.in_registry ? "" : ` (${m.id})`}
                              </option>
                            ))}
                          </optgroup>
                          {!role.local_only &&
                            roleOptions.cloud.map((p) => (
                              <optgroup key={p.provider} label={`${p.label} (${t.settings.cloudTag})`}>
                                {p.models.map((m) => (
                                  <option key={m} value={`${p.provider} ${m}`}>
                                    {m}
                                  </option>
                                ))}
                              </optgroup>
                            ))}
                        </select>
                      </div>
                    ))}
                </div>
              ))}
            </section>

            {/* ── Local runtime ─────────────────────────────────────── */}
            <section className="control-card settings-block">
              <div className="control-head">
                <span className="seclabel">{t.settings.localRuntimeTitle}</span>
                {available && (
                  <span className={`settings-status${(available.local_runtime?.ok ?? available.ollama.ok) ? " ok" : " bad"}`}>
                    {(available.local_runtime?.ok ?? available.ollama.ok)
                      ? `✓ ${available.local_runtime?.detail ?? available.ollama.detail}`
                      : `✗ ${t.settings.localRuntimeDown}`}
                  </span>
                )}
              </div>
              <p className="settings-note">{t.settings.localRuntimePrivacy}</p>
              <select
                className="fld"
                value={settings.local_runtime.kind}
                aria-label={t.settings.localRuntimeType}
                onChange={(e) => update((d) => {
                  d.local_runtime.kind = e.target.value as "ollama" | "openai_compatible";
                  d.local_runtime.base_url = e.target.value === "ollama"
                    ? "http://localhost:11434" : "http://127.0.0.1:1234/v1";
                })}
              >
                <option value="ollama">Ollama</option>
                <option value="openai_compatible">OpenAI-kompatibel (LM Studio / llama.cpp)</option>
              </select>
              <div className="addrow">
                <input
                  className="fld"
                  value={settings.local_runtime.base_url}
                  onChange={(e) => update((d) => { d.local_runtime.base_url = e.target.value; })}
                  placeholder={settings.local_runtime.kind === "ollama" ? "http://localhost:11434" : "http://127.0.0.1:1234/v1"}
                  aria-label={t.settings.localRuntimeUrl}
                />
                <button
                  className="control-pill"
                  onClick={() => testProvider("local", settings.local_runtime.base_url, settings.local_runtime.api_key, {
                    kind: settings.local_runtime.kind,
                    allow_private_network: settings.local_runtime.allow_private_network,
                  })}
                >
                  {t.settings.test}
                </button>
              </div>
              <div className="addrow">
                <input className="fld" value={settings.local_runtime.chat_model}
                  aria-label={t.settings.localChatModel} placeholder="Modell-id"
                  onChange={(e) => update((d) => { d.local_runtime.chat_model = e.target.value; })} />
                <input className="fld" type="password" value={settings.local_runtime.api_key}
                  aria-label={t.settings.localRuntimeKey}
                  placeholder={settings.local_runtime.has_key ? `${t.settings.keySaved} ${settings.local_runtime.key_hint ?? ""}` : t.settings.localRuntimeKey}
                  onChange={(e) => update((d) => { d.local_runtime.api_key = e.target.value; })} />
              </div>
              <div className="addrow">
                <input className="fld" value={settings.local_runtime.vision_model}
                  aria-label={t.settings.localVisionModel} placeholder={t.settings.localVisionModel}
                  onChange={(e) => update((d) => { d.local_runtime.vision_model = e.target.value; })} />
                <input className="fld" value={settings.local_runtime.embedding_model}
                  aria-label={t.settings.localEmbeddingModel} placeholder={t.settings.localEmbeddingModel}
                  onChange={(e) => update((d) => { d.local_runtime.embedding_model = e.target.value; })} />
              </div>
              <div className="addrow">
                <input className="fld" type="number" min={256}
                  value={settings.local_runtime.context_overrides[settings.local_runtime.chat_model] ?? ""}
                  aria-label={t.settings.localChatContext} placeholder={t.settings.localChatContext}
                  onChange={(e) => update((d) => {
                    const model = d.local_runtime.chat_model;
                    if (model && e.target.value) d.local_runtime.context_overrides[model] = Number(e.target.value);
                    else if (model) delete d.local_runtime.context_overrides[model];
                  })} />
                <input className="fld" type="number" min={256}
                  value={settings.local_runtime.context_overrides[settings.local_runtime.vision_model] ?? ""}
                  aria-label={t.settings.localVisionContext} placeholder={t.settings.localVisionContext}
                  onChange={(e) => update((d) => {
                    const model = d.local_runtime.vision_model;
                    if (model && e.target.value) d.local_runtime.context_overrides[model] = Number(e.target.value);
                    else if (model) delete d.local_runtime.context_overrides[model];
                  })} />
              </div>
              {settings.local_runtime.kind === "openai_compatible" && (
                <>
                  <label className="settings-toggle">
                    <input type="checkbox" checked={settings.local_runtime.allow_private_network}
                      onChange={(e) => update((d) => { d.local_runtime.allow_private_network = e.target.checked; })} />
                    {t.settings.allowPrivateNetwork}
                  </label>
                  <div className="settings-subhead">{t.settings.capabilities}</div>
                  <div className="addrow">
                    {(["tools", "vision", "embeddings", "structured_output"] as const).map((cap) => (
                      <label key={cap} className="settings-rolemeta">{cap}
                        <select className="fld" value={settings.local_runtime.capabilities[cap]}
                          onChange={(e) => update((d) => { d.local_runtime.capabilities[cap] = e.target.value as "supported" | "unsupported" | "unknown"; })}>
                          <option value="unknown">unknown</option>
                          <option value="supported">supported</option>
                          <option value="unsupported">unsupported</option>
                        </select>
                      </label>
                    ))}
                  </div>
                </>
              )}
              {testStatus["local"] && <div className="settings-note">{testStatus["local"]}</div>}
            </section>

            {/* ── Cloud providers ───────────────────────────────────── */}
            <section className="control-card settings-block">
              <div className="control-head">
                <span className="seclabel">{t.settings.cloudTitle}</span>
              </div>
              <p className="settings-note">{t.settings.cloudPrivacy}</p>
              {settings.cloud_providers.length === 0 && (
                <p className="settings-note">{t.settings.noProviders}</p>
              )}
              {settings.cloud_providers.map((p, i) => (
                <div key={p.id} className="settings-provider">
                  <div className="settings-provhead">
                    <input
                      className="fld settings-provlabel"
                      value={p.label}
                      aria-label={t.settings.providerName}
                      onChange={(e) => update((d) => { d.cloud_providers[i].label = e.target.value; })}
                    />
                    <label className="settings-toggle">
                      <input
                        type="checkbox"
                        checked={p.enabled}
                        onChange={(e) => update((d) => { d.cloud_providers[i].enabled = e.target.checked; })}
                      />
                      {t.settings.enabled}
                    </label>
                    <button
                      className="control-pill danger"
                      onClick={() =>
                        update((d) => {
                          d.cloud_providers.splice(i, 1);
                          for (const rid of Object.keys(d.roles)) {
                            if (d.roles[rid]?.provider === p.id) delete d.roles[rid];
                          }
                        })
                      }
                    >
                      {t.common.remove}
                    </button>
                  </div>
                  <input
                    className="fld"
                    value={p.base_url}
                    aria-label="Base URL"
                    placeholder="https://api.example.com/v1"
                    onChange={(e) => update((d) => { d.cloud_providers[i].base_url = e.target.value; })}
                  />
                  <div className="addrow">
                    <input
                      className="fld"
                      type="password"
                      value={p.api_key}
                      aria-label={t.settings.apiKey}
                      placeholder={p.has_key ? `${t.settings.keySaved} ${p.key_hint ?? ""}` : t.settings.apiKeyPlaceholder}
                      onChange={(e) => update((d) => { d.cloud_providers[i].api_key = e.target.value; })}
                    />
                    <button
                      className="control-pill"
                      onClick={() => testProvider(p.id, p.base_url, p.api_key)}
                    >
                      {t.settings.test}
                    </button>
                  </div>
                  {testStatus[p.id] && <div className="settings-note">{testStatus[p.id]}</div>}
                  <input
                    className="fld"
                    value={p.models.join(", ")}
                    aria-label={t.settings.models}
                    placeholder={t.settings.modelsPlaceholder}
                    onChange={(e) =>
                      update((d) => {
                        d.cloud_providers[i].models = e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean);
                      })
                    }
                  />
                </div>
              ))}
              <div className="addrow">
                <select className="fld" value={newPreset} onChange={(e) => setNewPreset(e.target.value)} aria-label={t.settings.presetLabel}>
                  {PROVIDER_PRESETS.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
                <button className="control-pill" onClick={addProvider}>{t.settings.addProvider}</button>
              </div>
            </section>

            {errors.length > 0 && (
              <div className="form-error" role="alert">
                {errors.map((e) => (
                  <div key={e}>{e}</div>
                ))}
              </div>
            )}

            <div className="settings-actions">
              <button className="control-pill" onClick={() => void load()}>{t.settings.reload}</button>
              <button
                className={`control-pill${dirty ? " on" : ""}`}
                disabled={saving || !dirty}
                onClick={() => void save()}
              >
                {saving ? t.settings.saving : t.settings.save}
              </button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
