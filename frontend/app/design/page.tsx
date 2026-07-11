"use client";

import { useState } from "react";
import "./design.css";
import {
  ArtifactCard,
  Badge,
  BrainPopover,
  BrowserFrame,
  Button,
  Card,
  CardHead,
  Chip,
  CommandPalette,
  Diff,
  EmptyState,
  Field,
  IconButton,
  Inspector,
  InspectorSection,
  Kbd,
  Logomark,
  Modal,
  Pill,
  SectionLabel,
  SegControl,
  Select,
  Spinner,
  Stat,
  Switch,
  Tabs,
  Terminal,
  ToolChip,
  Tooltip,
  WorkflowCard,
} from "@/components/ui";

/* ── showcase scaffolding ─────────────────────────────────────────── */

function Section({ id, title, desc, children }: { id: string; title: string; desc: string; children: React.ReactNode }) {
  return (
    <section className="dz__section" id={id}>
      <div className="dz__h">
        <h2>{title}</h2>
        <p>{desc}</p>
      </div>
      {children}
    </section>
  );
}

function Spec({ name, note, col, children }: { name: string; note?: string; col?: boolean; children: React.ReactNode }) {
  return (
    <div className="spec">
      <div className={`spec__demo${col ? " spec__demo--col" : ""}`}>{children}</div>
      <div className="spec__meta">
        <div className="spec__name">{name}</div>
        {note && <div className="spec__note">{note}</div>}
      </div>
    </div>
  );
}

/* ── foundation data ──────────────────────────────────────────────── */

const SURFACES = [
  ["--bg", "#0a0b0e"],
  ["--bg-soft", "#0e0f13"],
  ["--panel", "#14151b"],
  ["--panel-2", "#1a1b22"],
  ["--border", "#21222c"],
  ["--border-2", "#2c2d39"],
];
const ACCENTS = [
  ["--accent", "#7c8cff"],
  ["--cyan", "#4fd6e0"],
  ["--green", "#54d98c"],
  ["--violet", "#b69cff"],
  ["--amber", "#f6c453"],
  ["--red", "#f0857c"],
];
const SPACING = [
  ["--sp-2", 5],
  ["--sp-3", 8],
  ["--sp-4", 11],
  ["--sp-5", 14],
  ["--sp-6", 18],
  ["--sp-7", 22],
  ["--sp-8", 26],
  ["--sp-9", 34],
  ["--sp-10", 48],
];
const RADII = ["--r-xs", "--r-sm", "--r-md", "--r-lg", "--r-xl", "--r-2xl", "--r-3xl"];
const TYPE = [
  ["--fs-greet", "Bygg lokala agentflöden", { fontSize: "var(--fs-greet)", fontWeight: 760, letterSpacing: "var(--ls-display)" }],
  ["--fs-h2", "Sektionsrubrik", { fontSize: "var(--fs-h2)", fontWeight: 600 }],
  ["--fs-body", "Brödtext för gränssnittet, 15px.", { fontSize: "var(--fs-body)" }],
  ["--font-prose", "Agentens prosa sätts i serif.", { fontFamily: "var(--serif)", fontSize: "17px" }],
  ["--font-meta", "route=kod · gemma4:12b · 3 verktyg", { fontFamily: "var(--mono)", fontSize: "var(--fs-meta)", color: "var(--dim)" }],
];
const ELEV = ["--shadow-sm", "--shadow-md", "--shadow-lg", "--shadow-xl"];

/* ── page ─────────────────────────────────────────────────────────── */

export default function DesignSystem() {
  const [seg, setSeg] = useState("auto");
  const [tab, setTab] = useState("filer");
  const [sw1, setSw1] = useState(true);
  const [sw2, setSw2] = useState(false);
  const [sel, setSel] = useState("gemma4:12b");
  const [field, setField] = useState("");
  const [modal, setModal] = useState<null | "wide" | "narrow">(null);
  const [paletteDemo, setPaletteDemo] = useState(false);
  const [inspTab, setInspTab] = useState("orkestrering");
  const [brainMode, setBrainMode] = useState("auto");
  const [brainModel, setBrainModel] = useState("auto");
  const [brainAgent, setBrainAgent] = useState("claude");

  return (
    <div className="dz">
      <div className="dz__inner">
        <header className="dz__top">
          <Logomark size="lg" conic />
          <div>
            <div className="dz__title">Pilot Design System</div>
            <div className="dz__sub">Near-black, calm, developer-adjacent · svenskt UI · mono bär all meta</div>
          </div>
          <a className="dz__back" href="/">
            <Button variant="secondary" size="sm">← Till appen</Button>
          </a>
        </header>

        {/* ── FOUNDATIONS ── */}
        <Section id="surfaces" title="Ytor & linjer" desc="Lager av nästan-svart — canvas → paneler → hairlines.">
          <div className="dz__swatches">
            {[...SURFACES, ...ACCENTS].map(([name, val]) => (
              <div className="sw" key={name}>
                <div className="sw__chip" style={{ background: `var(${name})` }} />
                <div className="sw__meta">
                  <div className="sw__name">{name}</div>
                  <div className="sw__val">{val}</div>
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section id="gradient" title="Signaturgradient" desc="Indigo→cyan linjär + konisk — endast på små brand/action-element.">
          <div className="dz__grid">
            <Spec name="--grad" note="send-knapp, primär action, ✦-märke">
              <div style={{ height: 54, flex: 1, borderRadius: "var(--r-lg)", background: "var(--grad)" }} />
            </Spec>
            <Spec name="--grad-conic" note="stora hero-badgen">
              <div style={{ height: 54, flex: 1, borderRadius: "var(--r-lg)", background: "var(--grad-conic)" }} />
            </Spec>
            <Spec name="background-clip: text" note="hero-hälsningens betoning">
              <span style={{ fontSize: 30, fontWeight: 760, letterSpacing: "-1px", background: "var(--grad)", WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>
                lokala agentflöden
              </span>
            </Spec>
          </div>
        </Section>

        <Section id="type" title="Typografi" desc="System-sans för chrome, serif för agentprosa, mono för all meta.">
          <div className="dz__type">
            {TYPE.map(([tag, text, style]) => (
              <div className="type-row" key={tag as string}>
                <span className="type-row__tag">{tag as string}</span>
                <span style={style as React.CSSProperties}>{text as string}</span>
              </div>
            ))}
          </div>
        </Section>

        <Section id="spacing" title="Spacing, radier & elevation" desc="Kompakt rytm, mjuka radier, djupa nästan-svarta skuggor.">
          <div className="dz__grid">
            <Spec name="Spacing" note="2 → 48px; gutter 22px" col>
              <div className="dz__scale">
                {SPACING.map(([name, px]) => (
                  <div className="scale-row" key={name as string}>
                    <div className="scale-row__box" style={{ width: px as number }} />
                    <span className="scale-row__label">{name} · {px}px</span>
                  </div>
                ))}
              </div>
            </Spec>
            <Spec name="Radii" note="5px micro-tag → 18px composer → pill" col>
              <div className="dz__radii">
                {RADII.map((r) => (
                  <div className="radius-box" key={r} style={{ borderRadius: `var(${r})` }}>{r.replace("--r-", "")}</div>
                ))}
              </div>
            </Spec>
            <Spec name="Elevation" note="skuggor lyfter UI:t från canvasen" col>
              <div className="dz__elev">
                {ELEV.map((s) => (
                  <div className="elev-box" key={s} style={{ boxShadow: `var(${s})` }}>{s.replace("--shadow-", "")}</div>
                ))}
              </div>
            </Spec>
          </div>
        </Section>

        {/* ── CORE ── */}
        <Section id="core" title="Core — knappar, badges, pills" desc="Button, IconButton, Badge, Pill, Chip, SegControl.">
          <div className="dz__grid">
            <Spec name="Button" note="primary reserveras för den viktigaste åtgärden">
              <Button variant="primary">Skicka</Button>
              <Button variant="secondary">Sekundär</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger">Ta bort</Button>
            </Spec>
            <Spec name="Button · sizes">
              <Button size="sm">sm</Button>
              <Button size="md">md</Button>
              <Button size="lg">lg</Button>
              <Button disabled>disabled</Button>
            </Spec>
            <Spec name="IconButton" note="unicode-glyfer + valfri räknare">
              <IconButton glyph="☰" title="Meny" />
              <IconButton glyph="⏰" badge={3} title="Jobb" />
              <IconButton glyph="⚙" title="Inställningar" />
              <IconButton glyph="⟲" title="Ny konversation" />
              <IconButton glyph="◆" active title="Aktiv" />
            </Spec>
            <Spec name="Badge" note="label = mono micro-tag · soft = tonad chip">
              <Badge tone="cyan">kod</Badge>
              <Badge tone="dim">gemma4:12b</Badge>
              <Badge variant="soft" tone="green">✓ sparat</Badge>
              <Badge variant="soft" tone="cyan">live</Badge>
              <Badge variant="soft" tone="red">fel</Badge>
            </Spec>
            <Spec name="Pill" note="brain-/anslutningsindikator; busy = andas">
              <Pill busy>gemma4:12b</Pill>
              <Pill orb="green">Ansluten</Pill>
              <Pill orb="amber">Ansluter</Pill>
            </Spec>
            <Spec name="Chip" note="ghost = empty-state · reply = snabbsvar">
              <Chip>Granska diffen</Chip>
              <Chip variant="reply">Ja, kör</Chip>
            </Spec>
            <Spec name="SegControl" note="inset multi-toggle" col>
              <SegControl
                options={[{ value: "auto", label: "Auto" }, { value: "chat", label: "Chatt" }, { value: "computer", label: "Dator" }, { value: "code", label: "Kod" }]}
                value={seg}
                onChange={setSeg}
                aria-label="Läge"
              />
            </Spec>
          </div>
        </Section>

        {/* ── FORMS ── */}
        <Section id="forms" title="Forms — fält, select, switch" desc="Field, Select, Switch.">
          <div className="dz__grid">
            <Spec name="Field" note="inset fyllning, accent-fokusglöd" col>
              <Field placeholder="C:\sökväg\till\projekt" value={field} onChange={(e) => setField(e.target.value)} fullWidth />
              <Field multiline rows={2} placeholder="Instruktion till Pilot…" fullWidth />
            </Spec>
            <Spec name="Select" note="native select, egen ▾-chevron" col>
              <Select
                options={["auto", "gemma4:12b", "qwen2.5-coder:14b", "deepseek-r1:14b"]}
                value={sel}
                onChange={setSel}
                fullWidth
              />
            </Spec>
            <Spec name="Switch" note="gradient-spår när på" col>
              <Switch checked={sw1} onChange={setSw1} label="Aktivera jobb" />
              <Switch checked={sw2} onChange={setSw2} label="Vision (bild)" />
              <Switch checked disabled label="Låst" />
            </Spec>
          </div>
        </Section>

        {/* ── FEEDBACK ── */}
        <Section id="feedback" title="Feedback — orkestrering, verktyg, notiser" desc="ToolChip, Tooltip, Spinner, Toast (se appen).">
          <div className="dz__grid">
            <Spec name="ToolChip" note="verktygsnamn + trunkerade args" col>
              <ToolChip name="run_command" args="cmd=pnpm test · cwd=frontend" />
              <ToolChip name="read_file" args="path=README.md" />
              <ToolChip name="search_files" args="query=coordinator" />
            </Spec>
            <Spec name="Spinner" note="insyn / tänker">
              <Spinner size="sm" />
              <Spinner size="md" />
              <Spinner size="lg" />
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8, font: "12px var(--mono)", color: "var(--dim)" }}>
                <Spinner size="sm" /> arbetar…
              </span>
            </Spec>
            <Spec name="Tooltip" note="hover/fokus-etikett; triggern bär eget namn">
              <Tooltip label="Öppna session"><IconButton glyph="☰" aria-label="Öppna session" /></Tooltip>
              <Tooltip label="Schemalagda jobb" side="bottom"><IconButton glyph="⏰" aria-label="Schemalagda jobb" /></Tooltip>
            </Spec>
          </div>
        </Section>

        {/* ── ARTIFACTS ── */}
        <Section id="artifacts" title="Artefakter" desc="ArtifactCard med Terminal-, Diff- och BrowserFrame-bodies — verktygens inramade output.">
          <div className="dz__grid">
            <Spec name="ArtifactCard + Terminal" note="mono-header, tone-tag, copy/expand" col>
              <ArtifactCard title="Kommandoutdata" tag="term" tone="green" onCopy={() => {}} onExpand={() => {}}>
                <Terminal text={"$ pnpm test\n42 tests passed\nok · 3.2s"} />
              </ArtifactCard>
            </Spec>
            <Spec name="ArtifactCard + Diff" col>
              <ArtifactCard title="auth/token.py" tag="diff" tone="green" onCopy={() => {}}>
                <Diff text={"@@ -12,7 +12,4 @@\n-def check_a(t):\n-def check_b(t):\n-def check_c(t):\n+def verify_token(t):\n     return t.valid"} />
              </ArtifactCard>
            </Spec>
            <Spec name="ArtifactCard + BrowserFrame" note="skärmdumpar ramas i browser-chrome (traffic lights)" col>
              <ArtifactCard title="run_command · skärmbild" tag="live" tone="cyan">
                <BrowserFrame url="localhost:3000">
                  <div style={{ padding: "26px 16px", font: "12px var(--mono)", color: "var(--dim)", textAlign: "center" }}>
                    skärmdumpens innehåll
                  </div>
                </BrowserFrame>
              </ArtifactCard>
            </Spec>
            <Spec name="Terminal · error" col>
              <Terminal text={"Traceback (most recent call last):\n  ValueError: invalid token"} error />
            </Spec>
          </div>
        </Section>

        {/* ── NAV ── */}
        <Section id="nav" title="Navigation & skal" desc="Tabs, WorkflowCard, CommandPalette (⌘K), Inspector, BrainPopover.">
          <div className="dz__grid">
            <Spec name="Tabs" note="understruken flikrad, gradient-linje" col>
              <Tabs
                tabs={[{ value: "filer", label: "Filer" }, { value: "orkestrering", label: "Orkestrering" }, { value: "terminal", label: "Terminal" }]}
                value={tab}
                onChange={setTab}
                aria-label="Inspector"
              />
              <div style={{ padding: "12px 4px", color: "var(--dim)", fontSize: 13 }}>Aktiv flik: {tab}</div>
            </Spec>
            <Spec name="WorkflowCard" note="snabbstarter på empty state" col>
              <WorkflowCard glyph="▣" tone="cyan" title="Styr datorn" subtitle="se & klicka" />
              <WorkflowCard glyph="⌘" tone="violet" title="Skriv kod" subtitle="expertmodeller" />
              <WorkflowCard glyph="✦" tone="accent" title="Research" subtitle="djupdyk" />
            </Spec>
            <Spec name="CommandPalette" note="⌘K — sök, ↑/↓, Enter">
              <Button onClick={() => setPaletteDemo(true)}>Öppna paletten</Button>
            </Spec>
            <Spec name="Inspector (inline)" note="höger-slide-in i appen; inline här" col>
              <div style={{ height: 300, width: "100%" }}>
                <Inspector
                  title="Insyn"
                  inline
                  tabs={[{ value: "orkestrering", label: "Orkestrering" }, { value: "session", label: "Session" }]}
                  activeTab={inspTab}
                  onTab={setInspTab}
                >
                  {inspTab === "orkestrering" ? (
                    <InspectorSection label="Senaste turen">
                      <ToolChip name="run_command" args="cmd=pnpm test" />
                    </InspectorSection>
                  ) : (
                    <InspectorSection label="Session">
                      <Stat label="Status" value="Ansluten" />
                      <Stat label="Turer" value={4} />
                    </InspectorSection>
                  )}
                </Inspector>
              </div>
            </Spec>
            <Spec name="BrainPopover" note="läge + modell + agent — ett klick från brain-pillen" col>
              <BrainPopover
                mode={brainMode}
                modes={[{ value: "auto", label: "Auto" }, { value: "chat", label: "Chatt" }, { value: "computer", label: "Dator" }, { value: "code", label: "Kod" }]}
                onMode={setBrainMode}
                model={brainModel}
                models={[
                  { id: "auto", label: "Auto-orkestrering", orb: "grad" },
                  { id: "qwen2.5-coder", label: "qwen2.5-coder:14b", orb: "violet", tag: "kod" },
                  { id: "deepseek-r1", label: "deepseek-r1:14b", orb: "cyan", tag: "reasoning" },
                ]}
                onModel={setBrainModel}
                agent={brainAgent}
                agents={[{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]}
                onAgent={setBrainAgent}
                modelHint="Modell · auto rådfrågar experter per fråga"
              />
            </Spec>
          </div>
        </Section>

        {/* ── OVERLAY ── */}
        <Section id="overlay" title="Overlay" desc="Modal — suddig scrim, mono-header, a11y (fokusfälla + Escape).">
          <div className="dz__grid">
            <Spec name="Modal" note="delar appens tillgängliga Dialog">
              <Button onClick={() => setModal("wide")}>Öppna (wide)</Button>
              <Button variant="secondary" onClick={() => setModal("narrow")}>Öppna (narrow)</Button>
            </Spec>
          </div>
        </Section>

        {/* ── BRAND + LAYOUT ── */}
        <Section id="brand" title="Brand & layout" desc="Logomark, Kbd, SectionLabel, Stat, Card, EmptyState.">
          <div className="dz__grid">
            <Spec name="Logomark" note="✦ i gradient-ruta, tre storlekar + wordmark">
              <Logomark size="sm" />
              <Logomark size="md" wordmark />
              <Logomark size="lg" conic />
            </Spec>
            <Spec name="Kbd" note="kortkommandon">
              <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                <Kbd>⌘</Kbd><Kbd>K</Kbd>
                <span style={{ color: "var(--dim)", fontSize: 13, marginLeft: 6 }}>öppna paletten</span>
              </span>
            </Spec>
            <Spec name="SectionLabel" col>
              <SectionLabel>Senaste prompts</SectionLabel>
              <SectionLabel>Nytt jobb</SectionLabel>
            </Spec>
            <Spec name="Stat" note="etikett + mono-värde" col>
              <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
                <Stat label="Status" value="Ansluten" />
                <Stat label="Turer" value={12} />
                <Stat label="Senast" value="Tur 12" />
              </div>
            </Spec>
            <Spec name="Card + CardHead" col>
              <Card inset style={{ width: "100%" }}>
                <CardHead>
                  <SectionLabel>Projekt</SectionLabel>
                  <Badge tone="cyan">aktiv</Badge>
                </CardHead>
                <div style={{ color: "var(--dim)", fontSize: 13 }}>pilot · C:\Users\lucas\Code\pilot</div>
              </Card>
            </Spec>
            <Spec name="EmptyState" col>
              <EmptyState glyph="⏰" title="Inga jobb ännu" hint="Schemalägg en påminnelse eller en återkommande uppgift.">
                <Button size="sm" variant="primary">＋ Nytt jobb</Button>
              </EmptyState>
            </Spec>
          </div>
        </Section>
      </div>

      {paletteDemo && (
        <CommandPalette
          onClose={() => setPaletteDemo(false)}
          placeholder="byt projekt, modell, schemalägg jobb…"
          groups={[
            {
              label: "Navigera",
              items: [
                { icon: "⟲", label: "Ny konversation", onSelect: () => {} },
                { icon: "⏰", label: "Schemalagda jobb", hint: "2", onSelect: () => {} },
                { icon: "⚙", label: "Modellinställningar", onSelect: () => {} },
              ],
            },
            {
              label: "Läge",
              items: [
                { icon: "›", label: "Auto", hint: "aktiv", onSelect: () => {} },
                { icon: "›", label: "Kod", onSelect: () => {} },
              ],
            },
          ]}
        />
      )}

      {modal && (
        <Modal
          glyph={modal === "narrow" ? "◔" : "⌘"}
          title={modal === "narrow" ? "Huvudagentens kontext" : "Projekt, modell och agent"}
          width={modal}
          onClose={() => setModal(null)}
        >
          <div className="mb" style={{ padding: 18, color: "var(--dim)", fontSize: 14, lineHeight: 1.6 }}>
            <p>Detta är DS-modalen — samma tillgängliga <code>Dialog</code> som appen använder: fokusfälla, Escape stänger, fokus återställs.</p>
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <Button variant="primary" onClick={() => setModal(null)}>Klart</Button>
              <Button variant="secondary" onClick={() => setModal(null)}>Avbryt</Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
