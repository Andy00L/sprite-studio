# Shot Edit Translator

User wants to modify an existing shot. Translate to a structured update.

## INPUT
- Shot: {shot_json}
- User's request: "{user_text}"

## OUTPUT (JSON only)

{{
  "fields_changed": ["setting" | "action" | "camera" | "emotion" | "narration_excerpt" | "character_dialog"],
  "updated_shot": {{ ... full updated shot object ... }},
  "regenerate_reference_still": boolean,
  "regenerate_video": boolean (only true after user re-approves the timeline)
}}

## RULES

- regenerate_video must always be false at edit time. Video regeneration requires explicit timeline re-approval.
- regenerate_reference_still is true if any visual field changed (setting, action, camera, characters_present).
- If the user just changes narration text, only narration_excerpt changes, no regeneration needed.
