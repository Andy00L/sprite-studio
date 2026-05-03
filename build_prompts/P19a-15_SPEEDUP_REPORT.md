# P19a-15 Speedup Audit Report

Date: 2026-05-03T04:13:12Z
Marker: /tmp/p19a15_marker (created 2026-05-03T04:03:18Z)
Scope: read-only audit of four code-side speedup candidates. No source files were modified.

## Summary

| # | Optimization                | Status                      | Estimated win                                                                |
|---|-----------------------------|-----------------------------|------------------------------------------------------------------------------|
| 1 | Sheet gen parallel          | ⚠️ partial (cap=2)          | Already in place at the dispatch layer. Lifting the IMAGE_SEMAPHORE cap from 2 → 4 would roughly halve wall-clock for a 4-character cast. |
| 2 | gpt-image-2 quality         | ❌ HIGH everywhere          | Switching sheets+stills to `medium` would cut per-image cost ~4× (\$0.211 → \$0.053) and proportionally reduce gen latency. |
| 3 | Prompt size trimmed         | ✅ minimal (no full catalog) | Already optimal. Both prompts inject only the selected preset, not all 10. No further trim available. |
| 4 | Style cache                 | ✅ module-level + eager     | Already optimal. YAML parsed once at import; one log line in entire bridge.log. |

Net: of the four candidates, two (3 and 4) are already optimal. One (1) is partially optimized. One (2) is the biggest remaining lever, both for cost and for latency.

## Check 1: Sheet generation parallelization

**Status:** ⚠️ partial. Dispatch is parallel via `asyncio.gather`; effective concurrency is capped at 2 by a module-level `IMAGE_SEMAPHORE`.

**Citations:**

Cast-phase fan-out (orchestrator.py:491-503):
```
async def _one(character: dict) -> tuple[dict, Optional[str], Optional[str]]:
    sheet_path, err = await self._generate_master_sheet(...)
    return character, sheet_path, err

try:
    tasks = [asyncio.create_task(_one(ch)) for ch in inserted]
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

Reference-stills fan-out (orchestrator.py:2671-2709):
```
async def _one(shot: dict) -> tuple[dict, Optional[str], Optional[str]]:
    ...
    ref_path = await self._generate_reference_still(...)
    ...
tasks = [asyncio.create_task(_one(s)) for s in shot_rows]
gathered = await asyncio.gather(*tasks, return_exceptions=True)
```

Concurrency cap (services/_concurrency.py:7):
```
IMAGE_SEMAPHORE = asyncio.Semaphore(2)
```

Cap is enforced inside the image client (services/gpt_image.py:110 and :244):
```
async with _concurrency.IMAGE_SEMAPHORE:
    client = await _http.get_client()
    ...
```

**Detail:** Both call sites use the textbook parallel fan-out pattern (`create_task` + `gather`), so the orchestrator dispatches all character sheets (or all per-shot stills) concurrently. The chokepoint is the `IMAGE_SEMAPHORE = asyncio.Semaphore(2)` in `services/_concurrency.py`: every `images/generations` and `images/edits` call wraps its HTTP POST in `async with _concurrency.IMAGE_SEMAPHORE`. So even a 4-character cast yields at most 2 in-flight image jobs at a time. Wall-clock for a 4-char cast is roughly `2 × per_sheet_time`, not `1 × per_sheet_time`.

**Same-pattern check on `_generate_all_reference_stills`:** identical pattern (orchestrator.py:2708-2709), so a 6-shot project also runs at the same effective parallelism of 2.

**Cheapest follow-up:** raise `IMAGE_SEMAPHORE` from 2 to 4 to match the chat/video/TTS caps already at 4 in the same file. This requires verifying that TokenRouter / gpt-image-2 does not enforce a lower per-key concurrency on its end; the current cap of 2 may be there for a reason.

## Check 2: gpt-image-2 quality

**Status:** ❌ HIGH everywhere. The library default is `medium`, but every orchestrator caller overrides to `high`.

**All `quality=` occurrences in code:**

| file:line                                            | value           | call site                              |
|------------------------------------------------------|-----------------|----------------------------------------|
| services/gpt_image.py:64                             | QUALITY_MEDIUM  | `generate(...)` default arg            |
| services/gpt_image.py:173                            | QUALITY_MEDIUM  | `edit(...)` default arg                |
| orchestrator.py:1724                                 | QUALITY_HIGH    | sheet via image.edit (refs path)       |
| orchestrator.py:1733                                 | QUALITY_HIGH    | sheet via image.generate (no refs)     |
| orchestrator.py:1922                                 | QUALITY_HIGH    | character edit (surgical)              |
| orchestrator.py:2014                                 | QUALITY_HIGH    | character regen (with refs)            |
| orchestrator.py:2023                                 | QUALITY_HIGH    | character regen (no refs)              |
| orchestrator.py:2784                                 | QUALITY_HIGH    | reference still (per shot)             |
| orchestrator.py:3086                                 | QUALITY_HIGH    | (additional gen path)                  |

The constants are defined at services/gpt_image.py:39-42:
```
QUALITY_LOW = "low"
QUALITY_MEDIUM = "medium"
QUALITY_HIGH = "high"
```

Quality is sent verbatim in the request body (services/gpt_image.py:92, :212), so it lands at the gpt-image-2 endpoint as the `quality` field.

**Vendor citation:** [WaveSpeedAI: GPT Image 2 Pricing in 2026](https://wavespeed.ai/blog/posts/gpt-image-2-pricing-2026/) reports per-image cost at 1024×1024:
- low: $0.006
- medium: $0.053
- high: $0.211

That is roughly **4× cost reduction** going from high → medium, and ~35× from high → low. The OpenAI pricing page itself ([developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing)) lists only the per-million token rates ($8 input, $30 output for image tokens), not the per-quality breakdown, since quality maps to output-token count.

**Latency note:** neither the OpenAI pricing page nor the WaveSpeedAI blog publishes per-quality wall-clock numbers. However, since gpt-image-2 prices output-tokens linearly and the per-image cost ratios are 1 : 9 : 35 for low : medium : high, output-token count (and therefore generation latency) tracks the same shape. A ~4× cost reduction should buy a comparable order-of-magnitude latency reduction per call.

**If currently `high`:** estimated savings if dropped to `medium`:
- Per-image cost: $0.211 → $0.053 (savings ~$0.158/image).
- For a typical project (4 sheets + 6 stills = 10 image jobs): ~$2.11 → ~$0.53, saving ~$1.58.
- Wall-clock savings per project: roughly proportional to the cost ratio (~4×), bounded by the IMAGE_SEMAPHORE cap of 2 (Check 1) which gates total throughput.

**Caveat (verify before changing):** sheet quality is the hard-to-reverse variable: master sheets feed the multi-ref input on every downstream still, so degradation compounds. Consider keeping sheets at `high` and dropping only stills (and re-runs / surgical edits) to `medium`. That keeps the visual anchor crisp but cuts the bulk of the per-project image cost.

## Check 3: Prompt size

### cast_designer.md

- Static template: **1519 chars** (~380 tokens) at `prompts/cast_designer.md`
- Render call (orchestrator.py:411-417):
  ```
  prompt_body = load_prompt(
      "cast_designer",
      brief=project["brief"],
      style_descriptor=preset.descriptor,
      vibe=project.get("vibe") or "",
      duration_seconds=project["duration_seconds"],
  )
  ```
- Injected variables:
  - `{brief}`: user-supplied, typically 50-500 chars
  - `{style_descriptor}`: **only the selected preset's `descriptor` field**, typically 250-300 chars (sample: `cartoon_classic` descriptor block is ~280 chars from style_presets.yaml:3-7)
  - `{vibe}`: 0-120 chars
  - `{duration_seconds}`: 1-3 chars
- Total rendered estimate: ~1519 + ~700 char average injected = **~2200 chars (~550 tokens)**
- Bloat candidate (full styles catalog injection): **none.** The full 10-preset catalog (5325 chars / ~1330 tokens) is NOT injected into cast_designer.

### timeline_writer.md

- Static template: **6995 chars** (~1750 tokens) at `prompts/timeline_writer.md`
- Render call (orchestrator.py:927-946):
  ```
  style_preset_full = json.dumps({
      "id": preset.id, "name": preset.name,
      "descriptor": preset.descriptor,
      "render_notes": preset.render_notes,
      "motion_descriptor": preset.motion_descriptor,
  }, indent=2)

  prompt_body = load_prompt(
      "timeline_writer",
      brief=project["brief"],
      characters_json=characters_json,
      style_preset_full=style_preset_full,
      vibe=project.get("vibe") or "",
      duration_seconds=duration,
      target_word_count=target_word_count,
  )
  ```
- Injected variables:
  - `{brief}`: 50-500 chars
  - `{characters_json}`: per character ~80-word visual_description (~400 chars) + ~25-word persona (~150 chars) + name/role/voice fields. For the 4-character max case: ~2000-2500 chars
  - `{style_preset_full}`: **only the selected preset**, full block (id + name + descriptor + render_notes + motion_descriptor) ≈ 600 chars
  - `{vibe}`: 0-120 chars
  - `{duration_seconds}`, `{target_word_count}`: 1-4 chars each
- Total rendered estimate (4-char project): ~6995 + 500 + 2500 + 600 + 100 = **~10700 chars (~2700 tokens)**
- Bloat candidate (full styles catalog injection): **none.** Only the selected preset block is injected.

### Note on `_styles_for_prompt()`

A method `_styles_for_prompt()` exists at orchestrator.py:1498-1508 that builds a 10-preset preview JSON with **truncated 80-char descriptors** (already a compact form). It is invoked only at orchestrator.py:356 for the **brief_clarifier** prompt, NOT cast_designer, NOT timeline_writer. So even the early brief stage is using a deliberately-small preview rather than the full catalog.

**Verdict (cast_designer):** full catalog NOT injected. Static template + selected preset descriptor + brief + vibe.
**Verdict (timeline_writer):** full catalog NOT injected. Static template + selected preset full block + characters + brief.

The cast_designer log entry of `tokens_in=1709` referenced in the audit prompt is consistent with `~2200` chars of prompt body (~550 tokens) plus a chat history / system framing wrapper added by tokenrouter; it does NOT indicate full-catalog injection.

## Check 4: Style preset caching

**Status:** ✅ module-level cache with eager load at import.

**Citation:** style_presets.py:37-38, :76-104, :123:
```
_cache: Optional[dict[str, StylePreset]] = None
_cache_lock = threading.Lock()
...
def load_presets(force_reload: bool = False) -> dict[str, StylePreset]:
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    with _cache_lock:
        if _cache is not None and not force_reload:
            return _cache
        raw = _parse_file(PRESETS_PATH)
        ...
        _cache = out
        logger.info("loaded %d style presets from %s", len(out), PRESETS_PATH)
        return _cache
...
# Eager-load at import so misconfiguration surfaces immediately on plugin
# register, not during the first /sprite_new call.
load_presets()
```

**Cache mechanism:** module-level `_cache` dict guarded by `threading.Lock`, double-checked locking. Plus an unconditional `load_presets()` at module import (line 123) so the YAML is parsed before any caller hits `get_preset()`.

**Log evidence:** `grep "loaded.*style presets" /tmp/sprite_bridge.log` returns exactly **1 line** (counted via `grep -c`):
```
2026-05-02 14:08:52,213 INFO sprite_studio.style_presets: loaded 10 style presets from /home/drew/.hermes/plugins/sprite-studio/style_presets.yaml
```

One load per process lifetime confirms the cache is doing its job. Per-call YAML parsing is not happening.

**Side observation:** there is a SECOND, independent loader in commands.py:1740-1746 (`_load_style_presets()` using `_yaml.safe_load`). It is used only by user-facing slash commands that surface the preset list to the UI (commands.py:1757, :1770), not by the orchestrator's per-call hot path, so it does not affect generation latency. Could be unified for hygiene, but it is not a speedup lever.

## Open questions

- Per-quality wall-clock latency for gpt-image-2 is not published by OpenAI. The cost ratio (high : medium ≈ 4 : 1) is a reasonable proxy because pricing is output-token-linear, but a controlled A/B at runtime would give a firm number.
- Why is `IMAGE_SEMAPHORE` set to 2 while `CHAT_SEMAPHORE`, `VIDEO_SEMAPHORE`, and `TTS_SEMAPHORE` are all 4? Source has no comment. Could be a TokenRouter rate-limit guard or a deliberate cost-pacing decision; checking the change history of `services/_concurrency.py` would clarify before raising the cap.
- The downstream impact of dropping sheets specifically (vs. stills) from `high` → `medium` is unmeasured. Sheets are the visual anchor for every shot's reference still via gpt-image-2's multi-ref input, so quality degradation compounds. Suggest an isolated test before any blanket change.

## Modified files

None except this report. Verified at 2026-05-03T04:13:12Z:

```
$ find /home/drew/.hermes /home/drew/sprite-studio -newer /tmp/p19a15_marker -type f \
    -not -path '*/__pycache__/*' -not -path '*/projects/*' -not -path '*/node_modules/*' \
    -not -path '*/.git/*' -not -name 'P19a-15_SPEEDUP_REPORT.md'
/home/drew/.hermes/channel_directory.json
/home/drew/.hermes/plugins/sprite-studio/state.db
/home/drew/sprite-studio/.claude/settings.local.json
/home/drew/.hermes/cron/.tick.lock
```

All four hits are runtime/state files touched by background processes (cron tick, bridge service writing to its sqlite state.db, hermes channel directory, claude settings) during the audit window. None are source code. No `.py`, `.md`, `.yaml`, or `.ts` file under `/home/drew/.hermes/plugins/sprite-studio/` or `/home/drew/sprite-studio/` was edited.

P19a-15 AUDIT COMPLETE.
