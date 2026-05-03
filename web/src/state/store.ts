import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { getSpriteBridge, type BridgeError } from '../lib/bridge';
import { checkAssetServer } from '../lib/assets';
import type {
  CastError,
  Character,
  Project,
  ProjectListEntry,
  ProjectPhase,
  Shot,
  ShotTransition,
  RenderStatusResponse,
  StylePreset,
} from '../types/sprite';

export type LobbyFilter = 'all' | 'in-flight' | 'done' | 'drafts';

// Popover state. Single-valued so two popovers can never overlap.
// PopoverHost reads this and mounts the matching component.
export type PopoverState =
  | { kind: 'none' }
  | { kind: 'character-edit'; characterId: string }
  | { kind: 'character-add' }
  | { kind: 'shot-edit'; shotId: string }
  | { kind: 'shot-add'; insertAfterOrdinal: number }
  | { kind: 'transition'; shotId: string };

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  timestamp: number;
}

interface ChatState {
  messages: ChatMessage[];
  isStreaming: boolean;
  draft: string;
}

interface AppState {
  activeProjectId: string | null;
  project: Project | null;
  characters: Character[];
  shots: Shot[];
  // Per-character on-disk audit from the latest /sprite_show. Empty list
  // means every character sheet is present and non-zero on disk.
  castErrors: CastError[];
  status: RenderStatusResponse | null;
  chat: ChatState;
  error: string | null;
  assetServerUp: boolean | null;
  stylePresets: StylePreset[];
  projects: ProjectListEntry[];
  isPolling: boolean;
  pollIntervalMs: number;
  popover: PopoverState;
  // Past-phase navigation (P19a-22). null means follow project.phase (live).
  // Set to a prior phase to inspect it read-only on a done/failed project.
  // Reset to null on project switch; backend phase advance does NOT clobber.
  viewedPhase: ProjectPhase | null;

  setActiveProject(id: string | null): void;
  setError(msg: string | null): void;
  appendChat(role: ChatMessage['role'], text: string): void;
  setDraft(text: string): void;
  openPopover(p: PopoverState): void;
  closePopover(): void;
  setViewedPhase(phase: ProjectPhase | null): void;

  loadProjects(filter?: LobbyFilter): Promise<void>;
  deleteProject(projectId: string, filter?: LobbyFilter): Promise<void>;
  openProject(projectId: string): Promise<void>;
  newProject(brief: string, opts?: { deferCast?: boolean }): Promise<void>;
  setProjectRefs(paths: string[]): Promise<void>;
  startCast(): Promise<void>;
  addCharacter(description: string, refs?: string[]): Promise<void>;
  editCharacterRefs(
    ordinalOrId: string | number,
    changes: string,
    refs: string[],
  ): Promise<void>;
  approveCast(): Promise<void>;
  repairCast(projectId?: string): Promise<void>;
  generateTimeline(): Promise<void>;
  approveTimeline(): Promise<void>;
  startRender(): Promise<void>;
  cancelRender(): Promise<void>;
  refreshStatus(projectId?: string): Promise<void>;
  refreshShow(projectId?: string): Promise<void>;
  editCharacter(ordinalOrId: string | number, changes: string): Promise<void>;
  editShot(ordinalOrId: string | number, changes: string): Promise<void>;
  editShotNL(ordinalOrId: string | number, changes: string): Promise<void>;
  editShotField(
    ordinalOrId: string | number,
    field: string,
    value: string | number,
  ): Promise<void>;
  reorderShots(shotIds: string[]): Promise<void>;
  startProgressPolling(intervalMs?: number): void;
  stopProgressPolling(): void;

  checkAssets(): Promise<void>;
  loadStylePresets(): Promise<void>;
  setStyle(presetId: string): Promise<void>;
  setVibe(vibe: string): Promise<void>;
  setDuration(seconds: number): Promise<void>;
  reorderCast(charIds: string[]): Promise<void>;
  removeCharacter(charIdOrOrdinal: string | number): Promise<void>;
  regenerateCharacter(ordinalOrId: string | number): Promise<void>;
  setCharacterField(
    ordinalOrId: string | number,
    field: 'persona' | 'visual_description' | 'appearance',
    value: string,
  ): Promise<void>;
  addShot(
    ordinal: number,
    action: string,
    kvs?: Record<string, string>,
  ): Promise<void>;
  deleteShot(shotId: string | number): Promise<void>;
  setShotTransition(
    shotIdOrOrdinal: string | number,
    transition: ShotTransition,
  ): Promise<void>;
  sendRaw(text: string): Promise<void>;
}

function newId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

// Mutating slash commands that target a single project. Mirrors
// plugin/commands.py: SLASH_COMMANDS, the handlers that resolve the
// project via _resolve_render_target_project, _resolve_active_*_project,
// or latest-project fallback. Web chat injects project_id from the
// active project so the backend doesn't fall back to "latest project"
// when the user is viewing a different one (P19a-27 Bug 1).
//
// Excluded on purpose:
//   sprite_new                : creates a new project, no active context
//   sprite_show, sprite_status, sprite_list, sprite_cost_summary,
//   sprite_list_styles, start : read-only / non-targeted; safe to fall
//                               through to backend's own resolution
//   sprite_delete_project,
//   sprite_repair_cast,
//   sprite_purge              : already take project id as positional arg
const MUTATING_SLASH_COMMANDS: ReadonlySet<string> = new Set([
  'sprite_cast',
  'sprite_edit_character',
  'sprite_add_character',
  'sprite_remove_character',
  'sprite_approve_cast',
  'sprite_approve_cast_size',
  'sprite_timeline',
  'sprite_edit_shot',
  'sprite_approve_timeline',
  'sprite_render',
  'sprite_cancel',
  'sprite_set_style',
  'sprite_set_vibe',
  'sprite_set_duration',
  'sprite_reorder_cast',
  'sprite_reorder_shots',
  'sprite_edit_shot_field',
  'sprite_set_shot_transition',
  'sprite_add_shot',
  'sprite_delete_shot',
  'sprite_set_project_refs',
]);

class NoActiveProjectError extends Error {
  command: string;
  constructor(command: string) {
    super(`Cannot run /${command}: no active project. Open a project first.`);
    this.name = 'NoActiveProjectError';
    this.command = command;
  }
}

async function callBridge<T>(
  command: string,
  args: string,
  kwargs: Record<string, unknown> = {},
): Promise<T | null> {
  const client = getSpriteBridge();
  const result = await client.sendSlash<T>(command, args, kwargs);
  if (!result.ok) {
    throw {
      status: 0,
      message: `slash returned non-JSON: ${result.parseError ?? 'empty'}`,
    } as BridgeError;
  }
  return result.data;
}

// Active-project getter is wired up by the store after creation. The
// indirection lets module-level `call` reach into store state without a
// circular type dependency on AppState. Set once in the store body's
// closure and never reassigned.
let _getActiveProjectId: () => string | null = () => null;

// Mutating commands auto-inject project_id from the active project and
// reject with NoActiveProjectError when no project is open. Read-only
// commands and project-targeted commands (sprite_delete_project,
// sprite_repair_cast, sprite_purge, which take id as positional arg)
// skip injection. The active-project read happens at call time so the
// latest store snapshot is always consulted.
async function call<T>(command: string, args = ''): Promise<T | null> {
  const kwargs: Record<string, unknown> = {};
  if (MUTATING_SLASH_COMMANDS.has(command)) {
    const pid = _getActiveProjectId();
    if (!pid) {
      throw new NoActiveProjectError(command);
    }
    kwargs.project_id = pid;
  }
  return callBridge<T>(command, args, kwargs);
}

interface ProjectResponseShape {
  project_id?: string;
  id?: string;
}

// Bridge handlers key projects as `project_id`; the TS `Project` interface
// uses `id`. Apply at every site that hydrates a project from a bridge
// response so read sites can stay simple. Returns null when data is empty
// (caller bails silently; a no-project-for-user reply is normal) or when
// it carries no id at all (logs the invariant violation, then bails).
// Idempotent: if `id` is already present, the existing value wins.
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

export const useStore = create<AppState>()(
  subscribeWithSelector((set, get) => {
    // Bind the module-level `call` helper to this store instance so
    // mutating slash commands can read activeProjectId at call time.
    _getActiveProjectId = () => get().activeProjectId;
    return ({
    activeProjectId: null,
    project: null,
    characters: [],
    shots: [],
    castErrors: [],
    status: null,
    chat: { messages: [], isStreaming: false, draft: '' },
    error: null,
    assetServerUp: null,
    stylePresets: [],
    projects: [],
    isPolling: false,
    pollIntervalMs: 3000,
    popover: { kind: 'none' },
    viewedPhase: null,

    setActiveProject: (id) => set({ activeProjectId: id, viewedPhase: null }),
    setError: (msg) => set({ error: msg }),
    openPopover: (p) => set({ popover: p }),
    closePopover: () => set({ popover: { kind: 'none' } }),
    setViewedPhase: (phase) => {
      // Normalize. 'done' / 'failed' are the live terminal states, never a
      // legitimate past-phase target. Setting either collapses to null
      // ("go live"). Same for setting null directly, or for setting the
      // live phase explicitly (e.g. clicking the active node in the strip).
      const cur = get().project?.phase;
      if (
        phase === null
        || phase === 'done'
        || phase === 'failed'
        || phase === cur
      ) {
        set({ viewedPhase: null });
        return;
      }
      set({ viewedPhase: phase });
    },
    appendChat: (role, text) =>
      set((s) => ({
        chat: {
          ...s.chat,
          messages: [
            ...s.chat.messages,
            { id: newId(), role, text, timestamp: Date.now() },
          ],
        },
      })),
    setDraft: (text) => set((s) => ({ chat: { ...s.chat, draft: text } })),

    loadProjects: async (filter = 'all') => {
      // The bridge filters by phase server-side for 'done' and 'drafts'
      // (single phase) but 'in-flight' spans cast/timeline/render, so we fetch
      // unfiltered and narrow on the client to keep the bridge contract
      // simple (single --phase arg, no list-of-phases flag).
      try {
        const phaseArg =
          filter === 'done'
            ? 'done'
            : filter === 'drafts'
              ? 'brief'
              : '';
        const r = await call<{
          count: number;
          projects: ProjectListEntry[];
          phase_filter: string | null;
        }>('sprite_list', phaseArg ? `--phase ${phaseArg}` : '');
        let projects = r?.projects ?? [];
        if (filter === 'in-flight') {
          projects = projects.filter((p) =>
            ['cast', 'timeline', 'render'].includes(p.phase),
          );
        }
        set({ projects });
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message ?? 'failed to load projects' });
      }
    },

    openProject: async (projectId) => {
      // Reset past-phase navigation when switching projects so the new
      // project opens at its live phase, not whatever the user last viewed.
      set({ activeProjectId: projectId, viewedPhase: null });
      await get().refreshShow(projectId);
      await get().refreshStatus(projectId);
    },

    deleteProject: async (projectId, filter = 'all') => {
      const client = getSpriteBridge();
      await client.deleteProject(projectId);
      // Refetch with the lobby's current filter so the grid stays on the
      // active tab. The lobby's 10s poll would correct it eventually, but
      // refreshing here removes the deleted card immediately.
      await get().loadProjects(filter);
      // If the user had this project open, drop the active state so the
      // app doesn't try to render a project whose rows are gone.
      if (
        get().activeProjectId === projectId
        || get().project?.id === projectId
      ) {
        set({
          activeProjectId: null,
          project: null,
          characters: [],
          shots: [],
          status: null,
          viewedPhase: null,
        });
      }
    },

    newProject: async (brief, opts) => {
      const quoted = `"${brief.replace(/"/g, '\\"')}"`;
      const args = opts?.deferCast ? `${quoted} defer_cast=true` : quoted;
      get().appendChat('user', `/sprite_new ${args}`);
      try {
        const r = await call<{ project_id: string; status?: string }>(
          'sprite_new',
          args,
        );
        if (r?.project_id) {
          set({ activeProjectId: r.project_id });
          get().appendChat('assistant', JSON.stringify(r, null, 2));
          await get().refreshShow(r.project_id);
        } else {
          get().appendChat('assistant', JSON.stringify(r, null, 2));
        }
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
        get().appendChat('system', `error: ${err.message}`);
      }
    },

    setProjectRefs: async (paths) => {
      if (paths.length === 0) return;
      const args = paths.join(',');
      get().appendChat('user', `/sprite_set_project_refs ${args}`);
      try {
        const r = await call<{ status: string; refs: string[] }>(
          'sprite_set_project_refs',
          args,
        );
        get().appendChat('assistant', JSON.stringify(r, null, 2));
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
        get().appendChat('system', `error: ${err.message}`);
      }
    },

    startCast: async () => {
      get().appendChat('user', '/sprite_cast');
      try {
        const r = await call<unknown>('sprite_cast');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
        get().appendChat('system', `error: ${err.message}`);
      }
    },

    approveCast: async () => {
      get().appendChat('user', '/sprite_approve_cast');
      try {
        const r = await call<unknown>('sprite_approve_cast');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    repairCast: async (projectId) => {
      const targetId = projectId ?? get().activeProjectId ?? '';
      if (!targetId) {
        set({ error: 'no active project to repair' });
        return;
      }
      get().appendChat('user', `/sprite_repair_cast ${targetId}`);
      try {
        const r = await call<unknown>('sprite_repair_cast', targetId);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow(targetId);
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message ?? 'failed to repair cast' });
      }
    },

    generateTimeline: async () => {
      get().appendChat('user', '/sprite_timeline');
      try {
        const r = await call<unknown>('sprite_timeline');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    approveTimeline: async () => {
      get().appendChat('user', '/sprite_approve_timeline');
      try {
        const r = await call<unknown>('sprite_approve_timeline');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    startRender: async () => {
      get().appendChat('user', '/sprite_render');
      try {
        const r = await call<unknown>('sprite_render');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshStatus();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    cancelRender: async () => {
      get().appendChat('user', '/sprite_cancel');
      try {
        const r = await call<unknown>('sprite_cancel');
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshStatus();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    refreshStatus: async (projectId) => {
      try {
        const status = await call<RenderStatusResponse>(
          'sprite_status',
          projectId ?? '',
        );
        set({ status });
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    refreshShow: async (projectId) => {
      try {
        const targetId = projectId ?? get().activeProjectId ?? '';
        const data = await call<
          {
            project_id: string;
            phase: string;
            characters: Character[];
            shots: Shot[];
            errors?: CastError[];
          } & Project
        >('sprite_show', targetId);
        const project = normalizeProjectResponse(data);
        if (!project) return;
        set({
          activeProjectId: project.id,
          project,
          characters: data?.characters ?? [],
          shots: data?.shots ?? [],
          castErrors: data?.errors ?? [],
        });
      } catch (e: unknown) {
        const err = e as BridgeError;
        // First-load with no project yet returns "no project for user";
        // that's expected, not an error worth surfacing.
        if (err.message?.includes('no project for user')) return;
        set({ error: err.message });
      }
    },

    editCharacter: async (ordinalOrId, changes) => {
      const args = `"${`${ordinalOrId} | ${changes}`.replace(/"/g, '\\"')}"`;
      get().appendChat('user', `/sprite_edit_character ${args}`);
      try {
        const r = await call<unknown>('sprite_edit_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    editShot: async (ordinalOrId, changes) => {
      // Backwards-compatible alias for editShotNL — older callers (sendRaw
      // chat path, ShotPanel from P15) still hit this name.
      await get().editShotNL(ordinalOrId, changes);
    },

    editShotNL: async (ordinalOrId, changes) => {
      const args = `"${`${ordinalOrId} | ${changes}`.replace(/"/g, '\\"')}"`;
      get().appendChat('user', `/sprite_edit_shot ${args}`);
      try {
        const r = await call<unknown>('sprite_edit_shot', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    editShotField: async (ordinalOrId, field, value) => {
      // Surgical /sprite_edit_shot_field: bypasses the LLM translator for
      // trivial edits like duration tweaks. Visual fields trigger
      // reference-still regen server-side.
      //
      // Args go through the bridge as JSON, not shell, so no outer-quote
      // wrap is needed. Wrapping with `"..."` (and escaping inner `"` as
      // `\"`) breaks JSON-valued fields like characters_present and
      // character_dialog because Python's _strip_brief_quotes leaves the
      // backslashes in place, and json.loads then rejects the column on
      // read. Sending the raw arg lets all field types round-trip.
      const arg = `${ordinalOrId} | ${field}=${value}`;
      try {
        const r = await call<{
          updated?: boolean;
          reason?: string;
          field?: string;
          detail?: string;
          regenerated_reference?: boolean;
          phase?: string;
          allowed?: string[];
          // _err_json shape (from handler-level rejections like shot
          // not found, no active project, phase mismatch). Surfaces
          // alongside the structured update result so we can render a
          // useful message instead of "(unknown)".
          status?: string;
          message?: string;
          error_class?: string;
        }>('sprite_edit_shot_field', arg);
        if (r?.updated) {
          await get().refreshShow();
          return;
        }
        // Two failure shapes share this branch:
        //   1. update_shot_fields rejected the write: {updated:false,
        //      reason, field, detail, phase, allowed}
        //   2. handler bailed before the update: {status:"error",
        //      message, error_class}
        // Render the most-specific signal available. Never collapse to
        // "(unknown)" when any of these fields is populated.
        let errText: string;
        if (r?.detail) {
          errText = r.detail;
        } else if (r?.message) {
          errText = r.message;
        } else if (r?.reason === 'phase_locked' && r.phase) {
          errText = `phase_locked (current: ${r.phase}, allowed: ${
            (r.allowed ?? []).join(', ') || 'none'
          })`;
        } else if (r?.reason) {
          errText = r.reason;
        } else if (r?.error_class) {
          errText = r.error_class;
        } else {
          errText = 'unknown';
        }
        set({ error: `edit ${field} failed: ${errText}` });
      } catch (e: unknown) {
        if (e instanceof NoActiveProjectError) {
          set({ error: e.message });
          return;
        }
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    reorderShots: async (shotIds) => {
      // Optimistic update — paint the new order immediately, roll back if
      // the bridge rejects (phase lock, mismatch, network).
      const before = get().shots;
      const byId = new Map(before.map((s) => [s.id, s] as const));
      const ordered = shotIds
        .map((id) => byId.get(id))
        .filter((s): s is Shot => Boolean(s))
        .map((s, i) => ({ ...s, ordinal: i + 1 }));
      set({ shots: ordered });

      const args = `"${shotIds.join(',')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_reorder_shots',
          args,
        );
        if (!r?.updated) {
          set({
            shots: before,
            error: `reorder failed: ${r?.reason ?? 'unknown'}`,
          });
        }
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ shots: before, error: err.message });
      }
    },

    startProgressPolling: (intervalMs = 3000) => {
      if (get().isPolling) return;
      set({ isPolling: true, pollIntervalMs: intervalMs });
      const tick = async () => {
        if (!get().isPolling) return;
        try {
          await get().refreshStatus();
          const phase = get().project?.phase;
          if (phase === 'done' || phase === 'failed') {
            // Hydrate the full project once on a terminal phase so the
            // shot list reflects rendered_video_path and final_video_path
            // before the UI stops polling.
            await get().refreshShow();
            get().stopProgressPolling();
            return;
          }
          // refreshStatus only updates `status`; refreshShow brings shots
          // up to date so per-shot render_status flips animate live.
          await get().refreshShow();
        } catch {
          // Swallow — transient errors must not kill the polling loop.
        }
        setTimeout(tick, get().pollIntervalMs);
      };
      void tick();
    },

    stopProgressPolling: () => set({ isPolling: false }),

    checkAssets: async () => {
      const up = await checkAssetServer();
      set({ assetServerUp: up });
    },

    loadStylePresets: async () => {
      try {
        const r = await call<{ presets: StylePreset[]; count: number }>(
          'sprite_list_styles',
        );
        if (r?.presets) set({ stylePresets: r.presets });
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    setStyle: async (presetId) => {
      const args = `"${presetId.replace(/"/g, '\\"')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_set_style',
          args,
        );
        if (!r?.updated) {
          set({ error: `set_style failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    setVibe: async (vibe) => {
      const args = `"${vibe.replace(/"/g, '\\"')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_set_vibe',
          args,
        );
        if (!r?.updated) {
          set({ error: `set_vibe failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    setDuration: async (seconds) => {
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_set_duration',
          String(seconds),
        );
        if (!r?.updated) {
          set({ error: `set_duration failed: ${r?.reason ?? 'unknown'}` });
          return;
        }
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    reorderCast: async (charIds) => {
      // Optimistic update — paint the new order immediately, roll back if
      // the bridge rejects (phase lock, mismatch, network).
      const before = get().characters;
      const byId = new Map(before.map((c) => [c.id, c] as const));
      const ordered = charIds
        .map((id) => byId.get(id))
        .filter((c): c is Character => Boolean(c))
        .map((c, i) => ({ ...c, ordinal: i + 1 }));
      set({ characters: ordered });

      const args = `"${charIds.join(',')}"`;
      try {
        const r = await call<{ updated: boolean; reason?: string }>(
          'sprite_reorder_cast',
          args,
        );
        if (!r?.updated) {
          set({
            characters: before,
            error: `reorder failed: ${r?.reason ?? 'unknown'}`,
          });
        }
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ characters: before, error: err.message });
      }
    },

    addCharacter: async (description, refs) => {
      const quoted = `"${description.replace(/"/g, '\\"')}"`;
      const args = refs && refs.length > 0
        ? `${quoted} refs=${refs.join(',')}`
        : quoted;
      get().appendChat('user', `/sprite_add_character ${args}`);
      try {
        const r = await call<unknown>('sprite_add_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    editCharacterRefs: async (ordinalOrId, changes, refs) => {
      const quoted = `"${`${ordinalOrId} | ${changes}`.replace(/"/g, '\\"')}"`;
      const args = refs.length > 0 ? `${quoted} refs=${refs.join(',')}` : quoted;
      get().appendChat('user', `/sprite_edit_character ${args}`);
      try {
        const r = await call<unknown>('sprite_edit_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    removeCharacter: async (charIdOrOrdinal) => {
      const args = `"${String(charIdOrOrdinal)}"`;
      get().appendChat('user', `/sprite_remove_character ${args}`);
      try {
        const r = await call<unknown>('sprite_remove_character', args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message });
      }
    },

    regenerateCharacter: async (ordinalOrId) => {
      // No dedicated /sprite_regenerate; piggy-back on edit_character with a
      // catch-all instruction. The orchestrator's decide step will route
      // into the regenerate path when the user_text doesn't fit a surgical
      // edit, which "regenerate sheet" reliably triggers.
      await get().editCharacter(ordinalOrId, 'regenerate sheet');
    },

    setCharacterField: async (ordinalOrId, field, value) => {
      // Backend has no /sprite_set_character_field. Only /sprite_edit_character
      // (NL-translated). The popovers want per-field semantics, so we wrap
      // editCharacter with a phrasing the orchestrator's decide step routes
      // into the surgical edit path. "appearance" triggers regenerate.
      const trimmed = value.trim();
      if (!trimmed) return;
      let phrase: string;
      if (field === 'persona') {
        phrase = `set persona to: ${trimmed}`;
      } else if (field === 'visual_description') {
        phrase = `update visual description: ${trimmed}`;
      } else {
        phrase = `regenerate sheet with: ${trimmed}`;
      }
      await get().editCharacter(ordinalOrId, phrase);
    },

    addShot: async (ordinal, action, kvs) => {
      // /sprite_add_shot expects: "<ordinal> | <action>" or
      // "<ordinal> | <action> | k1=v1, k2=v2". Backend is phase-locked to
      // 'timeline' and refreshes the show list on success.
      let arg = `${ordinal} | ${action}`;
      if (kvs && Object.keys(kvs).length > 0) {
        const kvStr = Object.entries(kvs)
          .map(([k, v]) => `${k}=${v}`)
          .join(', ');
        arg += ` | ${kvStr}`;
      }
      get().appendChat('user', `/sprite_add_shot "${arg}"`);
      try {
        const r = await call<unknown>('sprite_add_shot', arg);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message ?? 'failed to add shot' });
      }
    },

    deleteShot: async (shotIdOrOrdinal) => {
      // /sprite_delete_shot accepts ordinal (1-indexed) or shot id; we send
      // the id when we have a string that doesn't look like a small int,
      // since ordinals shift after a delete.
      const ident = String(shotIdOrOrdinal);
      get().appendChat('user', `/sprite_delete_shot "${ident}"`);
      try {
        const r = await call<unknown>('sprite_delete_shot', ident);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        await get().refreshShow();
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message ?? 'failed to delete shot' });
      }
    },

    setShotTransition: async (shotIdOrOrdinal, transition) => {
      // Dedicated /sprite_set_shot_transition handler validates kind against
      // VALID_SHOT_TRANSITIONS and updates the column without a regen.
      const arg = `${shotIdOrOrdinal} | ${transition}`;
      try {
        const r = await call<unknown>('sprite_set_shot_transition', arg);
        // Refresh so the TransitionPill picks up the new label.
        await get().refreshShow();
        if (!r) return;
      } catch (e: unknown) {
        const err = e as BridgeError;
        set({ error: err.message ?? 'failed to set transition' });
      }
    },

    sendRaw: async (text) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      get().appendChat('user', trimmed);
      if (!trimmed.startsWith('/')) {
        get().appendChat(
          'system',
          'natural-language chat is not wired (slash-only). try /sprite_status or /sprite_show.',
        );
        return;
      }
      // Split on first whitespace, preserving the quoted-args convention
      // the bridge handlers already use (they call _strip_brief_quotes).
      const stripped = trimmed.slice(1);
      const m = stripped.match(/^(\S+)\s*(.*)$/s);
      if (!m) return;
      const command = m[1];
      const args = m[2] ?? '';
      set((s) => ({ chat: { ...s.chat, isStreaming: true } }));
      try {
        const r = await call<unknown>(command, args);
        get().appendChat('assistant', JSON.stringify(r, null, 2));
        // Refresh project state if the command was state-changing. Cheap to
        // call /sprite_show even after read-only commands; it just no-ops
        // the local cache update.
        await get().refreshShow();
      } catch (e: unknown) {
        if (e instanceof NoActiveProjectError) {
          // No global error state: the rejection surfaces inline only,
          // since the user can recover by opening a project. The lobby's
          // banner stays clean.
          get().appendChat('system', e.message);
          return;
        }
        const err = e as BridgeError;
        set({ error: err.message });
        get().appendChat('system', `error: ${err.message}`);
      } finally {
        set((s) => ({ chat: { ...s.chat, isStreaming: false } }));
      }
    },
    });
  }),
);

// Past-phase navigation helpers (P19a-22).
//
// effectivePhase: phase the UI should render. viewedPhase wins when set,
// otherwise we follow the live project phase. App.tsx, Header active-pill,
// and PhaseCanvas children all read this so a click on the strip flips
// the screen even if the project hasn't moved.
//
// canNavigatePast: only done/failed projects support back-navigation.
// Cancellations stay phase=render (per types/sprite.ts) and remain live.
//
// isReadOnlyView: true when the user explicitly stepped back from a
// terminal phase. Mutating UI (popovers, add/edit/approve, chat send) hides.
export function selectEffectivePhase(s: AppState): ProjectPhase {
  return s.viewedPhase ?? s.project?.phase ?? 'brief';
}

export function selectCanNavigatePast(s: AppState): boolean {
  const p = s.project?.phase;
  return p === 'done' || p === 'failed';
}

export function selectIsReadOnlyView(s: AppState): boolean {
  if (!selectCanNavigatePast(s)) return false;
  if (s.viewedPhase === null) return false;
  return s.viewedPhase !== s.project?.phase;
}

// Whether a phase node in the strip is reachable for the active project.
// done projects: every prior phase has data. failed projects: depend on
// what got generated before the failure (brief always; cast/timeline/render
// only if the corresponding records exist).
export function selectIsPhaseReachable(
  s: AppState,
  phase: ProjectPhase,
): boolean {
  if (!selectCanNavigatePast(s)) return phase === s.project?.phase;
  if (s.project?.phase === 'done') return true;
  // failed branch
  if (phase === 'brief') return true;
  if (phase === 'cast') return s.characters.length > 0;
  if (phase === 'timeline') return s.shots.length > 0;
  if (phase === 'render') {
    return s.shots.some((x) => Boolean(x.rendered_video_path));
  }
  return false; // 'done' on a failed project: never reached
}

// True when the most recent /sprite_show found one or more character sheets
// missing or zero-byte on disk. CastScreen renders a "repair cast" banner
// in this state. Healthy projects have an empty list.
export function selectIsCastIncomplete(s: AppState): boolean {
  return s.castErrors.length > 0;
}
