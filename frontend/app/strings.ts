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
    suggestions: [
      "Granska den här diffen och säg vad som är riskabelt",
      "Kör igenom repo:t och föreslå nästa tekniska steg",
      "Jämför lokala modeller och föreslå rätt standardstack",
      "Öppna projektet, kör testerna och förklara vad som faller",
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
};
