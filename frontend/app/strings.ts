// Central source of user-facing copy. Components reference these keys instead
// of inlining literals, so wording stays consistent and is easy to adjust or
// translate later. Current locale: Swedish (see <html lang> in layout.tsx).

export const LOCALE = "sv";

export const t = {
  appName: "Pilot",

  status: {
    disconnected: "Frånkopplad",
    connecting: "Ansluter",
    connected: "Ansluten",
    error: "Fel",
  },

  connection: {
    connecting: "Ansluter till Pilot…",
    dropped: "Anslutningen bröts. Försöker återansluta…",
    unauthorized: "Åtkomst nekad — token saknas eller är fel. Lägg till ?token=… i URL:en och försök igen.",
    retry: "Försök igen",
  },

  header: {
    openSession: "Öppna session",
    chooseProject: "Välj projekt",
    controlsHint: "Projekt, modell och agent (⌘K)",
    autoRoute: "Auto rutt",
    autoOrchestration: "auto-orkestrering",
    scheduledJobs: "Schemalagda jobb",
    newConversation: "Ny konversation",
  },

  hero: {
    titleLead: "Bygg, granska och kör ",
    titleAccent: "lokala agentflöden",
    tagline: "Pilot håller ihop chatt, kod, datorstyrning och modellval i ett enda arbetsflöde.",
    // Quick-start workflows on the empty state (DS WorkflowCard) — each seeds
    // one of Pilot's core flows.
    workflows: [
      { glyph: "▣", tone: "cyan", title: "Styr datorn", subtitle: "se & klicka", seed: "Ta en skärmbild och beskriv vad som visas på skärmen" },
      { glyph: "⌘", tone: "violet", title: "Skriv kod", subtitle: "expertmodeller", seed: "Öppna projektet, kör testerna och förklara vad som faller" },
      { glyph: "✦", tone: "accent", title: "Research", subtitle: "djupdyk", seed: "Jämför lokala modeller och föreslå rätt standardstack" },
    ],
  },

  composer: {
    placeholder: "Be Pilot om något, eller ge en tydlig uppgift…",
    working: "Pilot arbetar…",
    waiting: "Väntar på anslutning…",
    enterHint: "Enter skickar · Shift+Enter ny rad",
    context: "Kontext",
    send: "Skicka",
    abort: "Avbryt pågående körning",
  },

  messageActions: {
    copyPrompt: "Kopiera prompt",
    edit: "Redigera i rutan",
    resend: "Skicka igen",
    copyAnswer: "Kopiera svar",
    copy: "Kopiera",
    copied: "Kopierat.",
    copyFailed: "Kunde inte kopiera.",
  },

  drawer: {
    session: "Session",
    activeNow: "Aktiv nu",
    noProject: "Inget projekt valt",
    openControls: "Öppna kontrollpanelen för projekt, modell och agent.",
    statusLabel: "Status",
    turns: "Turer",
    last: "Senast",
    empty: "Tom",
    searchHistory: "Sök i historik",
    searchPlaceholder: "Filtrera tidigare prompts…",
    recentPrompts: "Senaste prompts",
    noMatches: "Inga träffar.",
    noPrompts: "Inga prompts ännu.",
    turn: "Tur",
  },

  routeLabel: {
    chat: "chatt",
    computer: "dator",
    code: "kod",
  },

  routeInsight: {
    toggle: "Varför den här rutten?",
    engine: "Motor",
    role: "Roll",
    model: "Modell",
    reason: "Skäl",
    fallback: "Reserv",
    permissions: "Behörigheter",
  },

  routeModes: [
    { id: "auto", label: "Auto" },
    { id: "chat", label: "Chatt" },
    { id: "computer", label: "Dator" },
    { id: "code", label: "Kod" },
  ],

  agents: [
    { id: "claude", label: "Claude Code" },
    { id: "codex", label: "Codex" },
  ],

  dialogs: {
    controls: "Projekt, modell och agent",
    context: "Huvudagentens kontext",
  },

  a11y: {
    skipToContent: "Hoppa till innehåll",
    jumpToLatest: "Hoppa till senaste meddelandet",
  },

  jumpLatest: "↓ Senaste",

  confirm: {
    reset: "Rensa konversationen och börja om?",
    resetAction: "Rensa",
    deleteJob: "Ta bort jobbet?",
    deleteJobAction: "Ta bort",
  },

  jobs: {
    none: "Inga jobb ännu.",
    taskPrefix: "uppgift · ",
    paused: " · pausad",
    pause: "Pausa jobb",
    resume: "Återuppta jobb",
    delete: "Ta bort jobb",
    newJob: "Nytt jobb",
    reminderKind: "Påminnelse",
    taskKind: "Uppgift",
    added: "Jobb tillagt.",
    needInstruction: "Skriv en instruktion till Pilot.",
    needReminder: "Skriv en påminnelsetext.",
    needWeekday: "Välj minst en veckodag.",
    badSchedule: "Kontrollera schemat innan du lägger till.",
    instructionPlaceholder: "Instruktion till Pilot…",
    reminderPlaceholder: "Påminnelsetext…",
  },

  projects: {
    adding: "Lägger till projekt…",
    needPath: "Ange en sökväg till projektet.",
    duplicate: "Projektet finns redan i listan.",
    recentPaths: "Senaste sökvägar",
    addProject: "＋ Lägg till projekt",
  },

  common: {
    close: "Stäng",
    add: "Lägg till",
    remove: "Ta bort",
  },

  palette: {
    placeholder: "byt projekt, modell, schemalägg jobb…",
    empty: "Inga träffar.",
    navigate: "Navigera",
    mode: "Läge",
    model: "Modell",
    agent: "Agent",
    active: "aktiv",
    newConversation: "Ny konversation",
    controls: "Projekt & kontroller",
    jobs: "Schemalagda jobb",
    settings: "Modellinställningar",
    context: "Huvudagentens kontext",
    inspector: "Inspector",
    design: "Designsystem",
  },

  inspector: {
    title: "Insyn",
    open: "Öppna inspector",
    tabs: {
      orchestration: "Orkestrering",
      artifacts: "Artefakter",
      session: "Session",
    },
    emptyOrchestration: "Inga steg ännu — ställ en fråga så visas orkestreringen här.",
    emptyArtifacts: "Inga artefakter i senaste turen.",
    lastTurn: "Senaste turen",
    sessionFacts: "Session",
    jobsLabel: "Jobb",
    project: "Projekt",
    noProject: "Inget projekt",
    model: "Modell",
    route: "Rutt",
    agent: "Agent",
    status: "Status",
    turns: "Turer",
  },

  settings: {
    title: "Modellinställningar",
    open: "Modellinställningar",
    intro:
      "Standardmodellen kör allt tills du ger en roll en egen modell. Lokala Ollama-modeller och molnleverantörer kan blandas fritt per roll.",
    loading: "Hämtar inställningar…",
    loadFailed: "Kunde inte hämta inställningarna. Kontrollera att backend är igång.",
    unauthorized: "Åtkomst nekad — kontrollera din token.",
    rolesTitle: "Roller",
    rolesHint:
      "Varje roll pekar på den modell som utför den. \"Ärver\" betyder att rollen följer standardmodellen.",
    agentRoles: "Agentroller (väljs per turtyp)",
    pipelineRoles: "Pipeline-steg (körs varje tur)",
    inheritDefault: "Ärver Standard",
    inheritEnv: "Standard (env)",
    localGroup: "Lokalt (Ollama)",
    cloudTag: "moln",
    ollamaTitle: "Ollama (lokalt)",
    ollamaUrl: "Ollama-URL",
    ollamaDown: "Ollama svarar inte",
    localRuntimeTitle: "Lokal modellruntime",
    localRuntimeType: "Runtime-typ",
    localRuntimeUrl: "Lokal runtime-URL",
    localRuntimeDown: "Lokal runtime svarar inte",
    localRuntimePrivacy:
      "Skärmbilder och embeddings skickas endast hit efter en strikt lokal nätverkskontroll. Publika endpoints avvisas alltid.",
    localRuntimeKey: "Lokal runtime-nyckel (valfri på loopback)",
    localChatModel: "Lokal chattmodell",
    localVisionModel: "Lokal visionmodell",
    localEmbeddingModel: "Lokal embeddingmodell",
    localChatContext: "Verifierat kontexttak (chatt)",
    localVisionContext: "Verifierat kontexttak (vision)",
    allowPrivateNetwork: "Tillåt privat nätverk (kräver separat runtime-nyckel)",
    capabilities: "Verifierade funktioner (okänd = avstängd)",
    cloudTitle: "Molnleverantörer",
    cloudPrivacy:
      "OBS: När en roll kör i molnet skickas insamlat underlag (fil-, skärm- och webbinnehåll) till den leverantören.",
    noProviders: "Inga molnleverantörer konfigurerade. Allt körs lokalt.",
    providerName: "Namn",
    enabled: "Aktiv",
    apiKey: "API-nyckel",
    apiKeyPlaceholder: "API-nyckel…",
    keySaved: "Nyckel sparad",
    models: "Modeller",
    modelsPlaceholder: "Modell-id:n, kommaseparerade (t.ex. gpt-4o-mini, gpt-4o)",
    presetLabel: "Leverantörsmall",
    addProvider: "＋ Lägg till leverantör",
    customProvider: "Anpassad (OpenAI-kompatibel)",
    test: "Testa",
    testing: "Testar…",
    testFailed: "Testet misslyckades",
    save: "Spara",
    saving: "Sparar…",
    saved: "Inställningarna sparade.",
    reload: "Läs om",
  },
};
