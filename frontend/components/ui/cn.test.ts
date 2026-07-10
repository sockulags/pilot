import { describe, it, expect } from "vitest";
import { cn } from "./cn";

describe("cn", () => {
  it("joins plain string classes with a single space", () => {
    expect(cn("ds-btn", "is-active")).toBe("ds-btn is-active");
  });

  it("drops falsy values (false, null, undefined, empty string, 0)", () => {
    expect(cn("a", false, null, undefined, "", 0, "b")).toBe("a b");
  });

  it("keeps conditional classes only when the condition is truthy", () => {
    const active = true;
    const disabled = false;
    expect(cn("ds-btn", active && "is-active", disabled && "is-disabled")).toBe(
      "ds-btn is-active",
    );
  });

  it("returns an empty string when given nothing", () => {
    expect(cn()).toBe("");
  });

  it("returns an empty string when every value is falsy", () => {
    expect(cn(false, null, undefined, "")).toBe("");
  });

  it("preserves the order of the classes it keeps", () => {
    expect(cn("first", "second", "third")).toBe("first second third");
  });
});
