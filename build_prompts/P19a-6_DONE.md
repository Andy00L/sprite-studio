# P19a-6 DONE: Fix project_id → id Mapping in Store Hydration

## 1. Bug summary

The bridge's `/sprite_show` handler returns the project keyed as `project_id`,
but the TypeScript `Project` interface declares `id: string`. The `refreshShow`
store action was casting the response object straight to `Project` without
remapping the key, so every component reading `project.id` got `undefined`.
The visible failure was DoneScreen requesting `/undefined/output/final.mp4`
from the asset server (404). Fix: a single helper `normalizeProjectResponse`
that maps `project_id` to `id` at the one and only hydration write site.

## 2. Files modified

| File | Lines added | Lines removed |
| --- | --- | --- |
| `web/src/state/store.ts` | +29 | -7 |

(Net +22 lines; 702 lines total after the change.)

No other source files modified.

## 3. Helper location and signature

Lives **inline in `web/src/state/store.ts`** between the `call<T>` helper and
the `useStore` factory (lines 127-151).

```ts
interface ProjectResponseShape {
  project_id?: string;
  id?: string;
}

function normalizeProjectResponse(
  data: ProjectResponseShape | null | undefined,
): Project | null {
  if (!data) return null;
  const id = data.id ?? data.project_id;
  if (typeof id !== 'string' || id.length === 0) {
    console.error(
      'normalizeProjectResponse: response missing project id',
      data,
    );
    return null;
  }
  return { ...data, id } as unknown as Project;
}
```

### Decision: inline vs separate file

Picked inline. The helper is currently called from exactly one place
(`refreshShow`) and is unlikely to be needed outside the store module given
that all other store actions hydrate transitively via `refreshShow`. Hoisting
it to `web/src/lib/projectShape.ts` would buy testability in isolation but
require a new file for code that has one caller. If a future prompt needs to
call this from outside the store (e.g., a test, or a non-zustand bridge
consumer), it can be hoisted then.

### Why no `[key: string]: unknown` index signature on the param type

The original draft used an index-signature catch-all so callers could pass
arbitrary response shapes. That fails to compile because the typed
`{project_id, phase, characters, shots} & Project` value the call site
produces does not declare an index signature, and TypeScript rejects the
assignment. Dropping the index signature lets any object with optional
`id` / `project_id` fields satisfy the parameter; the spread inside the
helper still copies every runtime own-property, and the
`as unknown as Project` cast at the return preserves the existing pattern.

## 4. Write sites fixed

Audited every store action against its bridge call. Result: the project is
hydrated from a bridge response in exactly **one** place. Every other action
that touches the project either calls `refreshShow` after its mutation
(transitively fixed) or constructs the project locally without a bridge
response (no helper needed; out of scope per the task spec).

| Action | Before (line) | What changed |
| --- | --- | --- |
| `refreshShow` | 304-310 | Replaced `if (!data || !data.project_id) return;` plus the `data as unknown as Project` cast with `const project = normalizeProjectResponse(data); if (!project) return;` and reads `project.id` for `activeProjectId`. The redundant guard is gone, the helper covers it. |
| `newProject` | 195-215 | Verified: no direct project hydration. Sets `activeProjectId` then awaits `refreshShow`. Clarification branch (no `project_id`) untouched, which is correct. |
| `openProject` | 189-193 | Verified: calls `refreshShow`. Transitively fixed. |
| `approveCast`, `generateTimeline`, `approveTimeline`, `editCharacter`, `editShot*`, `addCharacter`, `removeCharacter`, `regenerateCharacter`, `setCharacterField`, `addShot`, `deleteShot`, `setShotTransition`, `setStyle`, `setVibe`, `setDuration`, `reorderCast`, `reorderShots`, `sendRaw` | various | All call `refreshShow` after the bridge mutation. Transitively fixed. |
| `startRender`, `cancelRender` | 253-275 | Call `refreshStatus`, not `refreshShow` (status payload is unrelated). No project hydration here. |
| `App.tsx:88` (`project: null`) | n/a | Out of scope: this is a clear-on-back-to-lobby, not a bridge hydration. Left alone per task spec. |
| `LobbyScreen.tsx:47` (phantom Project for "+ new project") | n/a | Out of scope: locally-constructed Project with empty id, deliberate per the BriefScreen prefill contract. Left alone per task spec. |

No `project: data as unknown as Project` direct cast remains anywhere in the
store. Confirmed:

```
$ grep "as unknown as Project" src/state/store.ts
151:  return { ...data, id } as unknown as Project;
```

Only the helper itself does the cast.

## 5. Read sites audited

| File | Read pattern | Status |
| --- | --- | --- |
| `web/src/components/phases/DoneScreen.tsx:23` | `projectFinalVideoUrl(project.id, project.updated_at)` inside `if (videoUrl)` after `if (!project) return ...` | CLEAN. No fallback. The visible failure site; now resolves to a real ULID. |
| `web/src/components/phases/RenderScreen.tsx:37` | `const projectId = project.id;` after `if (!project) return ...` | CLEAN. No fallback. |
| `web/src/components/phases/BriefScreen.tsx:38` | `if (!project || !project.id) return ...` then reads other fields | CLEAN. The empty-string check guards the lobby phantom path. |
| `web/src/components/phases/CastScreen.tsx:11` | `const projectId = project?.id ?? '';` | CLEAN. Empty-string fallback produces a visibly broken URL (`//cast/...`), not a URL-shaped string that masks the bug. Spec explicitly allows `?? ''`. |
| `web/src/components/phases/TimelineScreen.tsx:50` | `const projectId = project?.id ?? '';` | CLEAN. Same as CastScreen. |
| `web/src/components/popovers/CharacterEditPopover.tsx:22` | `const projectId = project?.id ?? '';` | CLEAN. Same. |
| `web/src/components/chrome/Header.tsx` | reads `project?.phase`, `project?.title`, `project?.brief`, `project?.total_cost_usd` | N/A. Header does not read `project.id`. |
| `web/src/components/phases/LobbyScreen.tsx` | reads `ProjectListEntry` fields, not `Project` | N/A. The lobby uses the list shape (which already keys as `id`); not affected by this bug. |
| `web/src/lib/assets.ts` | URL builders take `projectId: string`. Callers pass `project.id` directly, never `project.id ?? '<literal>'`. | N/A. No changes. |

No site had a `?? 'undefined'` or any string fallback that produces a
valid-looking URL. No reads needed to be modified.

## 6. Type interface check

`web/src/types/sprite.ts:76`:

```ts
export interface Project {
  id: string;
  ...
}
```

`id` is already declared as required (`id: string`, no `?`). No edit needed.
The interface was correct; the hydration was wrong. Documented per task spec.

## 7. Smoke test results

The dev stack was restarted clean (kills on :8643/:9120/:5173, then `npm run
dev` from the repo root). The bridge logged `loaded plugin from
/home/drew/.hermes/plugins/sprite-studio` (27 commands) and `asset server
started on http://127.0.0.1:9120`.

This sandbox has no browser, so the smoke test was performed
**programmatically**: a Node script reproduced exactly the chain the front
end runs (call `/slash` with `sprite_show` for project D, run the
just-shipped `normalizeProjectResponse` against the response, build the
final video URL with `projectFinalVideoUrl`-equivalent template, then HEAD
that URL against the asset server).

| Step | Result |
| --- | --- |
| `/slash` `sprite_show` for project D returns `data` with `project_id`, no `id` | PASS. Top-level keys: `[brief, characters, error_message, final_video_path, final_video_size_bytes, music_track_path, narrator_script, phase, project_id, shots, title, total_cost_usd, use_narrator, xdg_open_hint]`. `data.id == null`, `data.project_id == '01KQMZKRZVVEW3RBEFP82BK4MG'`. |
| `normalizeProjectResponse(data)` returns project with `project.id === '01KQMZKRZVVEW3RBEFP82BK4MG'` | PASS. |
| `GET <assetBase>/<project.id>/output/final.mp4` returns 200 video/mp4 | PASS. Bridge access log: `"HEAD /01KQMZKRZVVEW3RBEFP82BK4MG/output/final.mp4 ... 200 332"`. |
| Pre-fix simulation: reading `data.id` directly produces `undefined` | CONFIRMED. URL would be `/undefined/output/final.mp4` (the original bug). Bridge access log shows that path returns 404 against the asset server: `"HEAD /undefined/output/final.mp4 ... 404 165"`. |

Cannot exercise the full DoneScreen render in a browser from this sandbox.
The hydration → URL → asset-server chain is the entire path that was broken,
and each link verifies independently. The change is mechanical (one cast
replaced by one helper call); no React-rendering edge case is plausible.

If a manual browser pass is needed: load `http://localhost:5173`, click
project D ("The Littlest Birthday Party"), confirm the bridge log shows
`GET /01KQMZKRZVVEW3RBEFP82BK4MG/output/final.mp4` (a real ULID) rather than
`/undefined/...`, and confirm the `<video>` element loads.

## 8. Backend untouched

```
$ find /home/drew/.hermes/plugins/sprite-studio -newer /tmp/p19a6_start_marker -type f \
    -not -path '*/__pycache__/*' \
    -not -path '*/projects/*'
(empty)
```

No plugin, bridge, or asset-server file was modified.

## 9. Backlog (out of scope, found during audit)

1. **`/sprite_show` response is missing several `Project` fields.** The
   handler returns only `project_id, phase, title, brief, use_narrator,
   narrator_script, total_cost_usd, characters, shots, final_video_path,
   final_video_size_bytes, music_track_path, error_message, xdg_open_hint`.
   The `Project` interface also expects `user_id, surface, style_preset_id,
   vibe, duration_seconds, created_at, updated_at, approved_cast_at,
   approved_timeline_at, rendered_at`. Most components do not read these and
   the cache-buster `updated_at` falls through to a no-query URL because of
   the `if (version)` truthy gate in `projectFinalVideoUrl`, so this is not
   a current visible bug. Worth either widening the bridge response or
   marking these fields optional in the TS interface. Out of scope for this
   prompt (changes the bridge contract or cascades through the type).
2. **`project.project_id` is preserved on the normalized result.** The
   spread `{ ...data, id }` retains `project_id` alongside the new `id`, so
   any code that has been (incorrectly) reading `project.project_id`
   continues to work. Should be removed once a sweep confirms no consumer
   reads `project.project_id`. Likely nobody does (TS would reject it on a
   `Project` value), but worth verifying with a future prompt before
   stripping.
3. **`RenderStatusResponse.project.project_id`** (in
   `web/src/types/sprite.ts:114`) keeps the bridge-side key. Consistent
   with the bridge contract; not a bug, but if the team decides to align
   the front end on `id` everywhere, this is the next place to look.

---

P19a-6 COMPLETE. See /home/drew/sprite-studio/build_prompts/P19a-6_DONE.md.
