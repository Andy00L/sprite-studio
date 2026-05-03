// Mirror of backend constants. If these drift, the smoke test catches it
// (popover save calls reject server-side with allowed-list errors).
//
// Sources:
//   ALLOWED_CAMERAS: orchestrator.py:65
//   DURATION_MIN/MAX: commands.py:1732 (also enforced by SQL CHECK)
//   TRANSITIONS: db.py:29 (VALID_SHOT_TRANSITIONS)

import type { ShotTransition } from '../types/sprite';

export const ALLOWED_CAMERAS: readonly string[] = [
  'static wide',
  'slow push-in',
  'pull-back reveal',
  'tracking',
  'handheld follow',
  'overhead',
  'low angle hero',
];

export const DURATION_MIN = 5;
export const DURATION_MAX = 15;

export const TRANSITIONS: readonly ShotTransition[] = [
  'cut',
  'fade',
  'dissolve',
  'match_cut',
];

export const CHARACTER_ROLES: readonly string[] = [
  'lead',
  'supporting',
  'comic_relief',
  'antagonist',
];
