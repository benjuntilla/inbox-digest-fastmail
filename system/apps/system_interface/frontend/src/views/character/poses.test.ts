import { describe, expect, it } from "vitest";
import { applyMood, ATTENDING_TILT, jump, posture, press, REST_TILT } from "./poses";
import { type BlobRig, createBlobRig } from "./rig";

const STEP = 1 / 60;

/** A rig that has been running long enough to be in its resting posture. */
function resting(): BlobRig {
  const rig = createBlobRig({ radius: 100 });
  posture(rig, false);
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
    applyMood(working, "working", false);
    const idle = resting();
    applyMood(idle, "idle", false);
    expect(liveliness(working, 4)).toBeGreaterThan(8);
    // And it is the work doing it, not the body being alive in general.
    expect(liveliness(idle, 4)).toBeLessThan(3);
  });

  it("settles again when the work stops", () => {
    const rig = resting();
    applyMood(rig, "working", false);
    for (let i = 0; i < 200; i++) rig.step(STEP);
    applyMood(rig, "idle", false);
    // Long enough for a held dent to spring out.
    for (let i = 0; i < 180; i++) rig.step(STEP);
    expect(liveliness(rig, 4)).toBeLessThan(3);
  });

  it("keeps the drawn-up lean while working", () => {
    const rig = resting();
    posture(rig, true);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    applyMood(rig, "working", true);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    expect(rotationOf(rig)).toBeCloseTo((ATTENDING_TILT * 180) / Math.PI, 0);
  });
});

describe("the user arriving and leaving", () => {
  it("draws the character up when the user arrives", () => {
    const rig = resting();
    expect(rotationOf(rig)).toBeCloseTo((REST_TILT * 180) / Math.PI, 0);
    posture(rig, true);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    expect(rotationOf(rig)).toBeCloseTo((ATTENDING_TILT * 180) / Math.PI, 0);
  });

  it("slouches back when the user leaves", () => {
    const rig = resting();
    posture(rig, true);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    posture(rig, false);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    expect(rotationOf(rig)).toBeCloseTo((REST_TILT * 180) / Math.PI, 0);
  });

  it("stands the body past vertical, since its own outline leans right", () => {
    // Guards the shape of the constant, not just its value: a fraction of the resting lean would
    // land at zero rotation, which still reads as leaning, because the body's own long axis runs
    // up and to the right before anything rotates it.
    expect(ATTENDING_TILT).toBeLessThan(0);
    expect(REST_TILT).toBeGreaterThan(0);
  });

  it("is idempotent, so a repeated answer costs nothing", () => {
    const rig = resting();
    posture(rig, true);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    const settled = rotationOf(rig);
    posture(rig, true);
    rig.step(STEP);
    expect(rotationOf(rig)).toBeCloseTo(settled, 1);
  });
});

describe("the arrival jump", () => {
  /** Ambient motion off, so a trace measures only the jump. */
  function still(): BlobRig {
    return createBlobRig({ radius: 100, idle: 0 });
  }

  /** Height above the floor and vertical squash, every frame for `seconds`. */
  function trace(rig: BlobRig, seconds: number): Array<{ t: number; up: number; sy: number }> {
    const rows: Array<{ t: number; up: number; sy: number }> = [];
    for (let i = 0; i < Math.round(seconds / STEP); i++) {
      rig.step(STEP);
      const f = rig.frame();
      const y = Number(/translate\(-?[\d.]+ (-?[\d.]+)\)/.exec(f.transform)?.[1] ?? 0);
      rows.push({ t: i * STEP, up: -y, sy: f.scale[1] });
    }
    return rows;
  }

  it("crouches on the floor, leaves it once, and comes back to rest", () => {
    const rig = still();
    jump(rig);
    const rows = trace(rig, 2.5);
    // The launch is 0.07s in; before it the body is squashed and still down.
    const gather = rows.filter((r) => r.t < 0.06);
    expect(Math.min(...gather.map((r) => r.sy))).toBeLessThan(0.92);
    expect(Math.max(...gather.map((r) => r.up))).toBeLessThan(2);

    const airborne = rows.map((r) => r.up > 2);
    const takeoffs = airborne.filter((isUp, i) => isUp && !airborne[i - 1]).length;
    expect(takeoffs).toBe(1);
    expect(Math.max(...rows.map((r) => r.up))).toBeGreaterThan(40);

    const last = rows[rows.length - 1];
    expect(last.up).toBeCloseTo(0, 0);
    expect(last.sy).toBeCloseTo(1, 1);
  });

  it("stretches on the way up, eases off at the apex, and folds on landing", () => {
    const rig = still();
    jump(rig);
    const rows = trace(rig, 2);
    const apex = rows.reduce((best, r) => (r.up > best.up ? r : best), rows[0]);
    const rising = rows.filter((r) => r.t < apex.t && r.up > 5);
    const landing = rows.filter((r) => r.t > apex.t && r.t < apex.t + 0.6);
    expect(Math.max(...rising.map((r) => r.sy))).toBeGreaterThan(1.1);
    expect(apex.sy).toBeLessThan(1);
    expect(apex.sy).toBeGreaterThan(0.85);
    expect(Math.min(...landing.map((r) => r.sy))).toBeLessThan(0.75);
  });

  it("keeps the posture it was holding", () => {
    const rig = resting();
    posture(rig, true);
    for (let i = 0; i < 300; i++) rig.step(STEP);
    const held = rotationOf(rig);
    jump(rig);
    for (let i = 0; i < 180; i++) rig.step(STEP);
    expect(rotationOf(rig)).toBeCloseTo(held, 0);
  });

  it("is cancelled by settling", () => {
    const rig = still();
    jump(rig);
    rig.settle();
    const rows = trace(rig, 1.5);
    expect(Math.max(...rows.map((r) => r.up))).toBeLessThan(1);
  });
});
