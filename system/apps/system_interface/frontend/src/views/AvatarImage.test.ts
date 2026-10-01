// @vitest-environment jsdom
import "../testing/dom";
import m from "mithril";
import { describe, expect, it } from "vitest";
import { appRecord, avatarStateRecord, windowRecord } from "../testing/records";
import type { TaskbarEntry } from "../reducers/desktopState";
import { entryStyleParts } from "./AvatarImage";
import type { ImbueCharacterAttrs } from "./character/ImbueCharacter";

const chat = appRecord("chat", {
  pin: { path: "/", style: "avatar", scope: "independent", default_mode: "floating" },
});

/** The pinned chat entry in the avatar style. */
function avatarEntry(overrides: Partial<TaskbarEntry> = {}): TaskbarEntry {
  return {
    window: windowRecord("win-1", "chat", "/", { is_pinned: true }),
    app: chat,
    title: "Chat",
    isMinimized: false,
    isDetached: false,
    isFocused: true,
    isPinned: true,
    look: { mode: "floating", style: "avatar", declaredStyle: "avatar", position: { x: 0.5, y: 0.5 } },
    ...overrides,
  };
}

describe("the character's attributes", () => {
  it("carry the mood and the class the entry chose", () => {
    const parts = entryStyleParts(avatarEntry(), avatarStateRecord({ design: "imbue-character" }), 32, "size-7");
    const attrs = (parts.image as m.Vnode<ImbueCharacterAttrs>).attrs;
    expect(attrs.mood).toEqual("idle");
    expect(attrs.class).toEqual("size-7");
  });
});
