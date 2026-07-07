import { describe, it, expect } from "vitest";
import { LOCALE, t } from "./strings";

describe("strings copy table", () => {
  it("declares the Swedish locale", () => {
    expect(LOCALE).toBe("sv");
  });

  it("exposes the app name", () => {
    expect(t.appName).toBe("Pilot");
  });

  it("resolves nested copy keys used by components", () => {
    expect(t.status.connected).toBe("Ansluten");
    expect(t.composer.send).toBe("Skicka");
    expect(t.messageActions.copied).toBe("Kopierat.");
  });

  it("provides four hero suggestion prompts, all non-empty", () => {
    expect(t.hero.suggestions).toHaveLength(4);
    for (const s of t.hero.suggestions) {
      expect(typeof s).toBe("string");
      expect(s.trim().length).toBeGreaterThan(0);
    }
  });

  it("keeps route modes and agents as {id,label} option lists", () => {
    const ids = t.routeModes.map((m) => m.id);
    expect(ids).toEqual(["auto", "chat", "computer", "code"]);
    for (const mode of t.routeModes) {
      expect(mode.id).toBeTruthy();
      expect(mode.label).toBeTruthy();
    }
    for (const agent of t.agents) {
      expect(agent.id).toBeTruthy();
      expect(agent.label).toBeTruthy();
    }
  });
});
