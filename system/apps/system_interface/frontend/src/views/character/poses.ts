/**
 * What the character does, per mood -- the layer between the workspace's
 * avatar status and the rig's verbs.
 *
 * Only the moods the workspace can actually emit are mapped here, and the rig
 * below carries only the channels these drive. Richer behaviours -- a glance, a
 * question, a jump -- are a matter of scoring the rig's verbs, and wait on
 * something in the workspace being able to ask for them.
 */
import type { BlobRig } from "./rig";

/** The moods the shell pushes (`avatar_status.mood`). */
export type CharacterMood = "idle" | "working";

/**
 * The resting lean, in radians, clockwise -- the character stands slightly
 * slanted to the right rather than square to the frame.
 *
 * Held on the tilt spring rather than baked into the silhouette, so anything
 * that wants it upright asks for a smaller tilt and springs there instead of
 * cutting. Subtler than the lean a turn takes (0.2): this one is a posture,
 * not a movement, and it is on screen the whole time.
 */
export const REST_TILT = 0.1;

/**
 * Hold the character's posture.
 *
 * Idempotent: the tilt is a spring target, so asking twice costs nothing, and
 * asking mid-motion lets the body travel to the new lean from wherever it is.
 */
export function posture(rig: BlobRig): void {
  rig.tilt(REST_TILT);
}

/**
 * Move the character to `mood`.
 *
 * Working is a condition, not an event: the surface goes unsettled and stays
 * that way for as long as there is work, pressing a shallow dent somewhere,
 * letting it out, and pressing somewhere else. Nothing marks the moment it
 * starts, because the signal behind this mood is "is any agent on the machine
 * running" -- it crosses at an instant the user has no reason to recognise. A
 * continuous state can be coarse and still read true; a gesture cannot.
 */
export function applyMood(rig: BlobRig, mood: CharacterMood): void {
  rig.configure({ busy: mood === "working" ? 1 : 0 });
  posture(rig);
}

/**
 * Press the character at `angle` (radians, from its centre): a dent where the
 * pointer landed, and a little give under the press.
 *
 * There is no drag counterpart: the character is a taskbar entry the user can
 * already drag to move, and two meanings for one gesture is one too many.
 */
export function press(rig: BlobRig, angle: number): void {
  rig.poke(angle, 1.1);
  rig.squish(0.22);
}

/** Let go of a press. */
export function releasePress(rig: BlobRig): void {
  rig.release();
}
