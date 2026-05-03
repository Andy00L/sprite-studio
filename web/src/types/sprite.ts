// The SQLite CHECK on projects.phase does not include 'cancelled'.
// Cancellations leave phase as 'render' and set error_message to "cancelled: ...".
export type ProjectPhase =
  | 'brief'
  | 'cast'
  | 'timeline'
  | 'render'
  | 'done'
  | 'failed';

export type CharacterRole =
  | 'lead'
  | 'supporting'
  | 'comic_relief'
  | 'antagonist';

export type ShotTransition = 'cut' | 'fade' | 'dissolve' | 'match_cut';

export interface Character {
  id: string;
  project_id: string;
  ordinal: number;
  name: string;
  role?: CharacterRole;
  persona: string;
  visual_description?: string;
  master_sheet_path?: string | null;
  voice_id?: string | null;
  voice_personality?: string | null;
  source?: 'generated' | 'reference_image' | 'reference_photo';
  reference_image_path?: string | null;
  is_approved: number;
  updated_at?: number;
}

export interface StylePreset {
  id: string;
  name: string;
  descriptor: string;
  render_notes?: string;
  motion_descriptor?: string;
  music_tag?: string;
  example_image?: string;
}

export interface DialogEntry {
  char_id: string;
  line: string;
}

export interface Shot {
  id: string;
  project_id: string;
  ordinal: number;
  duration_seconds: number;
  setting?: string;
  action: string;
  camera?: string | null;
  emotion?: string | null;
  characters_present: string[];
  dialog_speakers?: string[] | null;
  narration_line?: string | null;
  character_dialog?: DialogEntry[] | null;
  has_dialog: boolean;
  transition_to_next: ShotTransition;
  reference_still_path?: string | null;
  rendered_video_path?: string | null;
  render_status?: string;
  render_error?: string | null;
  cost_usd?: number | null;
  updated_at?: number;
}

// Backend /sprite_show derives this from {phase, len(shots)} so the frontend
// has a single field to drive the timeline-generation UX (poll while
// 'generating', render shots when 'ready', show retry on 'failed').
export type TimelineStatus =
  | 'not_started'
  | 'generating'
  | 'ready'
  | 'failed'
  | 'unknown';

export interface Project {
  id: string;
  user_id: string;
  surface: string;
  brief: string;
  style_preset_id: string;
  vibe?: string | null;
  duration_seconds: number;
  phase: ProjectPhase;
  title?: string | null;
  narrator_script?: string | null;
  use_narrator: boolean;
  music_track_path?: string | null;
  final_video_path?: string | null;
  total_cost_usd: number;
  created_at: number;
  updated_at: number;
  approved_cast_at?: number | null;
  approved_timeline_at?: number | null;
  rendered_at?: number | null;
  error_message?: string | null;
  timeline_status?: TimelineStatus;
}

// Per-character on-disk audit result surfaced by /sprite_show. A non-empty
// list means at least one character's master sheet is missing or zero-byte
// on disk despite the DB persisting a path. The CastScreen renders a
// "repair cast" banner when this is non-empty.
export interface CastError {
  character_id: string;
  name: string;
  error_class: 'sheet_missing_on_disk' | 'sheet_zero_bytes';
  path: string;
}

export interface ProgressEvent {
  project_id: string;
  timestamp: number;
  stage: string;
  detail: string;
  completed?: number;
  total?: number;
  error?: string | null;
}

export interface RenderStatusResponse {
  plugin: string;
  version: string;
  status: string;
  env_ok: boolean;
  project: {
    project_id: string;
    phase: ProjectPhase;
    title?: string | null;
    shots_done: number;
    shots_total: number;
    current_step: string;
    progress_detail?: string | null;
    progress_error?: string | null;
    total_cost_usd: number;
    eta_seconds?: number | null;
    final_video_path?: string | null;
    error_message?: string | null;
  } | null;
}

// Lobby card row: shape of one entry in the /sprite_list response. The bridge
// widens the projects table with a thumb_path (first shot's reference_still)
// when called with --with-thumbnail (P19a-0). Brief is truncated to 80 chars
// server-side; final_video_path is null until phase=done.
export interface ProjectListEntry {
  id: string;
  user_id?: string;
  surface?: string;
  brief: string;
  style_preset_id?: string;
  vibe?: string | null;
  duration_seconds?: number;
  phase: ProjectPhase;
  title?: string | null;
  total_cost_usd: number;
  created_at?: number;
  updated_at: number;
  rendered_at?: number | null;
  final_video_path?: string | null;
  thumb_path?: string | null;
}

// Response shape of /sprite_new when the brief clarifier asks for more info.
// Otherwise the handler auto-advances to cast and returns _format_cast_response.
export interface BriefClarification {
  status: 'needs_clarification';
  project_id: string;
  phase: 'brief';
  questions: string[];
  auto_decisions?: {
    style_preset_id?: string;
    duration_seconds?: number;
    vibe?: string;
  };
  next_steps?: string;
}
