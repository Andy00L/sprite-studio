# Character Edit Translator

The user wants to modify an existing character. Translate their natural-language request
into a structured edit plan.

## INPUT
- Original character: {character_json}
- User's edit request: "{user_text}"

## OUTPUT (JSON only)

{{
  "type": "surgical" | "regenerate",
  "rationale": "string (why we picked this type)",
  "updated_visual_description": "string (full new description if regenerating, or null if surgical)",
  "edit_prompt": "string (only if surgical: the natural-language edit command for gpt-image-2/edit, focused on what changes while preserving everything else)",
  "changed_fields": ["array", "of", "field", "names", "that", "changed"]
}}

## DECISION RULES

Use SURGICAL when:
- User changes a specific visible attribute (color, clothing item, expression, accessory)
- User wants to preserve the rest of the character

Use REGENERATE when:
- User changes species, age, or body type
- User changes more than 3 attributes at once
- User says "redo it" or "different character"

## EDIT PROMPT TEMPLATE (for surgical)

"In the provided character model sheet, {{specific change in plain language}}.
Preserve everything else: face, body shape, pose, palette outside the changed area,
background, framing, layout, all 4 panels of the model sheet."

## EXAMPLES

User: "Make the trench coat dark blue instead of beige"
→ surgical, edit_prompt: "In the provided character model sheet, change the trench coat color from beige to deep navy blue. Preserve everything else: the cat's body, fur pattern, face, pose in each of the 4 panels, and the off-white background."

User: "Change Mira to a small dog instead of a cat"
→ regenerate, updated_visual_description: "..." (full new description)

User: "Older, with reading glasses"
→ surgical, edit_prompt: "In the provided character model sheet, age the character to look mid-50s with subtle wrinkles around the eyes, and add small round reading glasses. Preserve clothing, body, palette, pose, and background."
