import { describe, expect, it } from "vitest";
import { applyMood, posture, press, REST_TILT } from "./poses";
import { type BlobRig, createBlobRig } from "./rig";

const STEP = 1 / 60;

/** A rig that has been running long enough to be in its resting posture. */
function resting(): BlobRig {
  const rig = createBlobRig({ radius: 100 });
  posture(rig);
  for (let i = 0; i < 180; i++) rig.step(STEP);
  return rig;
}

/** How far the frame's transform rotates the body, in degrees. */
function rotationOf(rig: BlobRig): number {
  const match = /rotate\((-?[\d.]+)/.exec(rig.frame().transform);
  return match === null ? 0 : Number(match[1]);
}

describe("the resting posture", () => {
  it("leans the body to the right", () => {
    // Positive degrees rotate clockwise in SVG, so a right lean is positive.
    expect(rotationOf(resting())).toBeCloseTo((REST_TILT * 180) / Math.PI, 0);
  });

  it("is what the body returns to after a press", () => {
    const rig = resting();
    const before = rotationOf(rig);
    press(rig, 0);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    expect(rotationOf(rig)).toBeCloseTo(before, 0);
  });
});

describe("a press", () => {
  it("dents the body where the pointer landed", () => {
    const pressed = resting();
    const untouched = resting();
    press(pressed, 0);
    for (let i = 0; i < 6; i++) {
      pressed.step(STEP);
      untouched.step(STEP);
    }
    expect(pressed.frame().d).not.toEqual(untouched.frame().d);
  });

  it("dents different sides for different angles", () => {
    const left = resting();
    const right = resting();
    press(left, Math.PI);
    press(right, 0);
    for (let i = 0; i < 6; i++) {
      left.step(STEP);
      right.step(STEP);
    }
    expect(left.frame().d).not.toEqual(right.frame().d);
  });
});

describe("moving to a mood", () => {
  /**
   * The fastest the outline moves in any short window over `seconds`.
   *
   * A short window is what separates the two: the resting lumps drift and the
   * body breathes, but slowly, so over a quarter of a second they barely move.
   * A dent arriving moves the surface by most of its depth in that time.
   */
  function liveliness(rig: BlobRig, seconds: number, window = 0.25): number {
    let worst = 0;
    for (let w = 0; w < Math.round(seconds / window); w++) {
      const before = rig.frame().anchors.map((a) => Math.hypot(a.x, a.y));
      for (let i = 0; i < Math.round(window / STEP); i++) rig.step(STEP);
      const after = rig.frame().anchors.map((a) => Math.hypot(a.x, a.y));
      worst = Math.max(worst, ...after.map((r, i) => Math.abs(r - before[i])));
    }
    return worst;
  }

  it("keeps the surface unsettled for as long as there is work", () => {
    const working = resting();
    applyMood(working, "working");
    const idle = resting();
    applyMood(idle, "idle");
    expect(liveliness(working, 4)).toBeGreaterThan(8);
    // And it is the work doing it, not the body being alive in general.
    expect(liveliness(idle, 4)).toBeLessThan(3);
  });

  it("settles again when the work stops", () => {
    const rig = resting();
    applyMood(rig, "working");
    for (let i = 0; i < 200; i++) rig.step(STEP);
    applyMood(rig, "idle");
    // Long enough for a held dent to spring out.
    for (let i = 0; i < 180; i++) rig.step(STEP);
    expect(liveliness(rig, 4)).toBeLessThan(3);
  });
});
