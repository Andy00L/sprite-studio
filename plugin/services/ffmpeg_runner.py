"""ffmpeg / ffprobe wrapper for Sprite Studio's render pipeline.

All functions are sync; callers wrap with asyncio.to_thread when run from
async context. Subprocesses use absolute paths, never shell=True.

Environment dependency: ffmpeg + ffprobe on PATH. Verified at module import.
"""
from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .errors import FFmpegError, FileInvalidError

logger = logging.getLogger("sprite_studio.services.ffmpeg")

# Module-load check.
_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
if not _FFMPEG or not _FFPROBE:
    raise ImportError(
        "ffmpeg/ffprobe missing on PATH. "
        "Install ffmpeg (apt: ffmpeg) before loading sprite-studio."
    )

# Defaults
DEFAULT_RESOLUTION = (1080, 1920)         # 9:16 portrait
DEFAULT_FPS = 24
DEFAULT_VIDEO_CRF = 21
DEFAULT_VIDEO_PRESET = "fast"
DEFAULT_AUDIO_BITRATE = "192k"
DEFAULT_AUDIO_SAMPLE_RATE = 44100
STDERR_TAIL_CHARS = 500
TIMEOUT_PER_OUTPUT_SECOND = 30  # 60s output -> 30min subprocess timeout

# Shot-to-shot transition fusion (P19-pre).
TRANSITION_DURATION_S = 0.5
# Map persisted transition_to_next values onto ffmpeg xfade transition names.
# 'cut' and 'match_cut' are absent: both render as a hard cut (match_cut
# is a semantic-only label kept for downstream analytics). Values not in
# this map fall through to the concat-in-filter (hard cut) branch.
TRANSITION_TO_XFADE = {
    "fade": "fade",
    "dissolve": "dissolve",
}


# --------------- low-level ---------------

def _run(
    cmd: list[str],
    *,
    timeout: int,
    description: str,
) -> subprocess.CompletedProcess:
    """Run a subprocess with stdout+stderr captured. Raise FFmpegError on
    non-zero exit or timeout. Never use shell=True.
    """
    if cmd[0] not in (_FFMPEG, _FFPROBE) and not Path(cmd[0]).is_absolute():
        raise FFmpegError(f"refusing to run non-absolute binary: {cmd[0]!r}")

    logger.info("%s: %s", description, " ".join(shlex.quote(c) for c in cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(
            f"{description} timed out after {timeout}s"
        ) from e

    if proc.returncode != 0:
        tail = (proc.stderr or "")[-STDERR_TAIL_CHARS:]
        raise FFmpegError(
            f"{description} failed (exit {proc.returncode}): ...{tail}"
        )
    return proc


def _validate_input(path: Path) -> None:
    """Reject missing/empty files BEFORE handing to ffmpeg. Better errors."""
    if not path.exists():
        raise FileNotFoundError(f"input does not exist: {path}")
    if path.stat().st_size == 0:
        raise FileInvalidError(f"input is 0 bytes: {path}")
    if "'" in str(path):
        raise FileInvalidError(
            f"input path contains single quote (breaks concat manifest): {path}"
        )


# --------------- probe ---------------

def probe(path: Path) -> dict:
    """Return parsed ffprobe JSON for the given media file."""
    _validate_input(path)
    cmd = [
        _FFPROBE,
        "-v", "error",
        "-of", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = _run(cmd, timeout=30, description=f"ffprobe {path.name}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise FileInvalidError(f"ffprobe output not JSON for {path}: {e}") from e


def get_duration_seconds(path: Path) -> float:
    """Convenience: extract duration from probe output."""
    info = probe(path)
    fmt = info.get("format", {})
    dur = fmt.get("duration")
    if dur is None:
        raise FileInvalidError(f"no duration in probe of {path}")
    return float(dur)


# --------------- concat manifest ---------------

def _build_concat_manifest(inputs: list[Path], manifest_path: Path) -> None:
    """Write a concat-demuxer manifest. One line per input, format:
        file 'absolute/path/to/file'
    Single quotes inside paths are rejected upstream by _validate_input.
    """
    lines = []
    for p in inputs:
        abs_p = p.resolve()
        lines.append(f"file '{abs_p}'")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------- concat_videos ---------------

def concat_videos(
    inputs: list[Path],
    output: Path,
    *,
    target_resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    target_fps: int = DEFAULT_FPS,
) -> None:
    """Concat multiple MP4s, normalize to target resolution/fps, encode H.264.

    Uses concat demuxer (not the concat filter) for inputs that share codec;
    re-encodes after to handle resolution/fps mismatches between shots.
    """
    if not inputs:
        raise ValueError("concat_videos: no inputs")
    if len(inputs) == 1:
        # Edge case: single input still needs normalization to target res.
        logger.info("concat_videos: 1 input, single-pass normalize")

    for p in inputs:
        _validate_input(p)
        # ffprobe each input - surfaces corrupted MP4s early
        probe(p)

    output.parent.mkdir(parents=True, exist_ok=True)
    w, h = target_resolution

    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "concat.txt"
        _build_concat_manifest(inputs, manifest)

        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={target_fps}"
        )

        cmd = [
            _FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(manifest),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", DEFAULT_VIDEO_PRESET,
            "-crf", str(DEFAULT_VIDEO_CRF),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", DEFAULT_AUDIO_BITRATE,
            "-ar", str(DEFAULT_AUDIO_SAMPLE_RATE),
            "-movflags", "+faststart",
            str(output),
        ]

        # Estimate timeout: 30s per output second, min 60s, max 1h.
        try:
            est_dur = sum(get_duration_seconds(p) for p in inputs)
        except FileInvalidError:
            est_dur = 60.0
        timeout = max(60, min(3600, int(est_dur * TIMEOUT_PER_OUTPUT_SECOND)))

        _run(cmd, timeout=timeout, description=f"concat_videos -> {output.name}")

    if not output.exists() or output.stat().st_size == 0:
        raise FFmpegError(f"concat_videos produced no output at {output}")


# --------------- concat_audios ---------------

def concat_audios(
    inputs: list[Path],
    output: Path,
) -> None:
    """Concat audio files. Uses -c copy when codecs match; re-encodes to mp3
    otherwise. Safe for the chunked-TTS path (all eleven_multilingual_v2 mp3
    outputs share codec/sample rate so -c copy works).
    """
    if not inputs:
        raise ValueError("concat_audios: no inputs")

    for p in inputs:
        _validate_input(p)

    # Probe codecs to decide copy vs re-encode
    codecs = set()
    for p in inputs:
        info = probe(p)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "audio":
                codecs.add(stream.get("codec_name"))
                break

    output.parent.mkdir(parents=True, exist_ok=True)
    use_copy = len(codecs) == 1

    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "concat.txt"
        _build_concat_manifest(inputs, manifest)

        cmd = [
            _FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(manifest),
        ]
        if use_copy:
            cmd += ["-c", "copy"]
        else:
            cmd += [
                "-c:a", "libmp3lame",
                "-b:a", DEFAULT_AUDIO_BITRATE,
                "-ar", str(DEFAULT_AUDIO_SAMPLE_RATE),
            ]
        cmd.append(str(output))

        try:
            est_dur = sum(get_duration_seconds(p) for p in inputs)
        except FileInvalidError:
            est_dur = 60.0
        timeout = max(60, min(1800, int(est_dur * TIMEOUT_PER_OUTPUT_SECOND)))

        _run(cmd, timeout=timeout, description=f"concat_audios -> {output.name}")

    if not output.exists() or output.stat().st_size == 0:
        raise FFmpegError(f"concat_audios produced no output at {output}")


# --------------- ducking envelope ---------------

def build_ducking_volume_expr(
    *,
    base_volume: float,
    ducked_volume: float,
    dialog_windows: list[tuple[float, float]],
) -> str:
    """Build a ffmpeg `volume` filter expression that ducks during dialog windows.

    Uses chained between() calls. Returns the value of the `volume` parameter
    (no filter name prefix). Caller wraps with f"volume={expr}:eval=frame".

    Example with 2 windows [(5.0,12.0), (20.0,28.0)]:
        if(between(t,5.0,12.0),0.35,if(between(t,20.0,28.0),0.35,0.85))
    """
    if not dialog_windows:
        return f"{base_volume}"

    # Sort and merge overlapping windows
    sorted_w = sorted(dialog_windows, key=lambda x: x[0])
    merged: list[tuple[float, float]] = []
    for start, end in sorted_w:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    expr = f"{base_volume}"
    for start, end in reversed(merged):
        expr = f"if(between(t,{start:.3f},{end:.3f}),{ducked_volume},{expr})"
    return expr


# --------------- shot transition fusion ---------------

def _has_visual_transitions(transitions: Optional[list[str]]) -> bool:
    """Returns True when at least one transition between shots will be a
    visual crossfade (fade/dissolve). 'cut' and 'match_cut' both render as
    hard cuts and don't require the xfade pipeline.

    The last transition in the list is ignored: it would describe the
    transition INTO a non-existent next shot.
    """
    if not transitions or len(transitions) < 2:
        return False
    return any(t in TRANSITION_TO_XFADE for t in transitions[:-1])


def _xfade_videos(
    inputs: list[Path],
    transitions: list[str],
    output: Path,
    *,
    target_resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    target_fps: int = DEFAULT_FPS,
) -> None:
    """Concat MP4s with per-pair transitions (cut or xfade) into one MP4.

    `transitions[i]` is the transition INTO inputs[i+1]; `transitions[-1]`
    is ignored. Values in TRANSITION_TO_XFADE produce a video xfade +
    audio acrossfade overlap of TRANSITION_DURATION_S; everything else
    (including 'cut' and 'match_cut') is a hard concat join.

    All inputs are normalized to target resolution / fps / yuv420p / sar=1
    and audio to fltp stereo 48kHz before joining, so heterogeneous
    Seedance outputs combine cleanly. Shots without audio get an
    anullsrc placeholder of matching duration.

    This is the Pass-1 alternative to concat_videos when the timeline
    contains any visual transition. The output goes through the same
    Pass-2 mixer in stitch_final, which preserves narration/music/card
    behavior.
    """
    if not inputs:
        raise ValueError("_xfade_videos: no inputs")
    if len(inputs) != len(transitions):
        raise ValueError(
            f"_xfade_videos: transitions length {len(transitions)} != "
            f"inputs length {len(inputs)}"
        )
    n = len(inputs)
    w, h = target_resolution

    for p in inputs:
        _validate_input(p)

    # Pre-probe each shot: durations drive xfade offsets, audio presence
    # drives the silent-placeholder injection below.
    durations: list[float] = []
    has_audio: list[bool] = []
    for p in inputs:
        info = probe(p)
        fmt = info.get("format", {})
        dur = fmt.get("duration")
        if dur is None:
            raise FileInvalidError(f"no duration in probe of {p}")
        durations.append(float(dur))
        has_audio.append(any(
            s.get("codec_type") == "audio"
            for s in info.get("streams", []) or []
        ))

    output.parent.mkdir(parents=True, exist_ok=True)

    # Inputs: shot files first (indexes 0..n-1), then anullsrc inputs for
    # shots without audio (mapped by silence_input_idx).
    input_args: list[str] = []
    for p in inputs:
        input_args += ["-i", str(p)]
    silence_input_idx: dict[int, int] = {}
    next_idx = n
    for i, has_a in enumerate(has_audio):
        if not has_a:
            input_args += [
                "-f", "lavfi",
                "-t", f"{durations[i]:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
            silence_input_idx[i] = next_idx
            next_idx += 1

    # Per-input normalization. Without this xfade rejects pairs whose
    # resolution / pix_fmt / fps / SAR / sample format / channel layout /
    # timebase don't match. Seedance is consistent in practice but
    # defensive normalization is cheap. settb/asettb is required because
    # chained concat-in-filter promotes the accumulator timebase to
    # AV_TIME_BASE (1/1000000), and a freshly-normalized xfade input
    # stays at 1/<fps>; xfade then rejects the pair. Per-input settb is
    # the defensive half of the fix; the post-concat re-assertion below
    # is the load-bearing half.
    fc_parts: list[str] = []
    for i in range(n):
        fc_parts.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={target_fps},format=yuv420p,"
            f"settb=1/{target_fps}[v{i}]"
        )
        a_src = silence_input_idx.get(i, i)
        fc_parts.append(
            f"[{a_src}:a]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asettb=1/48000[a{i}]"
        )

    # Sequential pairwise join. Each step pulls in [v_i][a_i] and produces
    # a new accumulator [vj_i][aj_i]. The xfade offset is measured on the
    # ACCUMULATED video timeline (which shrinks every time a fade is used);
    # cumulative_v_dur tracks that running length.
    cur_v = "v0"
    cur_a = "a0"
    cumulative_v_dur = durations[0]
    for i in range(1, n):
        prev_kind = transitions[i - 1]
        xfade_kind = TRANSITION_TO_XFADE.get(prev_kind)
        next_v = f"vj{i}"
        next_a = f"aj{i}"

        if xfade_kind is None:
            # Hard cut. concat-in-filter so the output streams stay inside
            # this filtergraph (the demuxer-concat would require a manifest
            # file and a separate ffmpeg invocation). Re-assert the
            # timebase on the accumulator: concat promotes its output to
            # AV_TIME_BASE, which would mismatch any subsequent xfade
            # input still at 1/<fps>.
            fc_parts.append(
                f"[{cur_v}][v{i}]concat=n=2:v=1:a=0,"
                f"settb=1/{target_fps}[{next_v}]"
            )
            fc_parts.append(
                f"[{cur_a}][a{i}]concat=n=2:v=0:a=1,"
                f"asettb=1/48000[{next_a}]"
            )
            cumulative_v_dur += durations[i]
        else:
            offset = cumulative_v_dur - TRANSITION_DURATION_S
            if offset < 0:
                # xfade requires offset >= 0. If the previous accumulator
                # is shorter than the overlap (very rare with our 5s min
                # shots) fall back to a hard cut to avoid crashing.
                logger.warning(
                    "_xfade_videos: shot %d offset %.3f < 0; falling back "
                    "to cut for this pair",
                    i, offset,
                )
                fc_parts.append(
                    f"[{cur_v}][v{i}]concat=n=2:v=1:a=0,"
                    f"settb=1/{target_fps}[{next_v}]"
                )
                fc_parts.append(
                    f"[{cur_a}][a{i}]concat=n=2:v=0:a=1,"
                    f"asettb=1/48000[{next_a}]"
                )
                cumulative_v_dur += durations[i]
            else:
                fc_parts.append(
                    f"[{cur_v}][v{i}]xfade=transition={xfade_kind}:"
                    f"duration={TRANSITION_DURATION_S}:"
                    f"offset={offset:.3f}[{next_v}]"
                )
                fc_parts.append(
                    f"[{cur_a}][a{i}]"
                    f"acrossfade=d={TRANSITION_DURATION_S}[{next_a}]"
                )
                cumulative_v_dur = (
                    cumulative_v_dur + durations[i] - TRANSITION_DURATION_S
                )
        cur_v = next_v
        cur_a = next_a

    filter_complex = ";".join(fc_parts)

    cmd = [_FFMPEG, "-y", *input_args] + [
        "-filter_complex", filter_complex,
        "-map", f"[{cur_v}]",
        "-map", f"[{cur_a}]",
        "-c:v", "libx264",
        "-preset", DEFAULT_VIDEO_PRESET,
        "-crf", str(DEFAULT_VIDEO_CRF),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", DEFAULT_AUDIO_BITRATE,
        "-ar", str(DEFAULT_AUDIO_SAMPLE_RATE),
        "-movflags", "+faststart",
        str(output),
    ]

    timeout = max(60, min(3600, int(cumulative_v_dur * TIMEOUT_PER_OUTPUT_SECOND)))
    _run(cmd, timeout=timeout, description=f"_xfade_videos -> {output.name}")

    if not output.exists() or output.stat().st_size == 0:
        raise FFmpegError(f"_xfade_videos produced no output at {output}")


# --------------- stitch_final ---------------

def stitch_final(
    *,
    shots: list[Path],
    narration: Optional[Path],
    music: Optional[Path],
    title_card: Optional[Path],
    end_card: Optional[Path],
    output: Path,
    target_resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    target_fps: int = DEFAULT_FPS,
    music_volume: float = 0.12,
    narration_volume: float = 0.85,
    narration_ducked_volume: float = 0.35,
    dialog_windows: Optional[list[tuple[float, float]]] = None,
    title_card_seconds: float = 1.5,
    end_card_seconds: float = 1.5,
    transitions: Optional[list[str]] = None,
) -> None:
    """Final stitch: shots -> concat (or xfade) -> mix narration + music + optional cards.

    Args:
        shots: ordered list of shot MP4s. Audio (Seedance dialog) is preserved
               from each shot.
        narration: full narration MP3 (None if project.use_narrator=False).
        music: background music file (None to skip music layer).
        title_card / end_card: PNG paths to bookend (None to skip).
        dialog_windows: list of (start_seconds, end_seconds) tuples on the
                        FINAL TIMELINE (shot offsets), where narration ducks.
                        Caller computes from shots metadata + has_dialog flags.
                        None or empty -> no ducking.
        narration_ducked_volume: volume during dialog windows (default 0.35).
        transitions: optional per-shot transition kinds aligned with `shots`.
                     transitions[i] is the transition INTO shots[i+1];
                     transitions[-1] is ignored. Values 'fade'/'dissolve'
                     trigger the xfade pass-1 pipeline (overlap of
                     TRANSITION_DURATION_S between adjacent shots);
                     'cut'/'match_cut'/None -> hard cut. When all transitions
                     are hard cuts (or transitions is None) the original
                     concat-demuxer pass-1 path is used (faster, well-tested).

    The shots' own audio (character dialog from Seedance) is included via
    amix; narration is mixed in additively with the ducking envelope.
    """
    if not shots:
        raise ValueError("stitch_final: no shots")
    if transitions is not None and len(transitions) != len(shots):
        raise ValueError(
            f"stitch_final: transitions length {len(transitions)} != "
            f"shots length {len(shots)}"
        )

    for p in shots:
        _validate_input(p)
    if narration is not None:
        _validate_input(narration)
    if music is not None:
        _validate_input(music)
    if title_card is not None:
        _validate_input(title_card)
    if end_card is not None:
        _validate_input(end_card)

    output.parent.mkdir(parents=True, exist_ok=True)
    w, h = target_resolution
    dialog_windows = dialog_windows or []
    use_xfade_pass1 = _has_visual_transitions(transitions)

    # Build the pipeline as two passes for clarity and predictability:
    #   pass 1: concat OR xfade shots with normalized resolution -> /tmp/_concat.mp4
    #   pass 2: mix narration + music + (optionally) cards -> output
    # This is more debuggable than one giant filtergraph and the cost is one
    # extra encode (~5-10% overhead, acceptable for our 60-90s outputs).

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        concat_path = tmp_dir / "_concat.mp4"
        if use_xfade_pass1:
            _xfade_videos(
                shots,
                transitions or [],
                concat_path,
                target_resolution=target_resolution,
                target_fps=target_fps,
            )
        else:
            concat_videos(
                shots,
                concat_path,
                target_resolution=target_resolution,
                target_fps=target_fps,
            )

        concat_duration = get_duration_seconds(concat_path)

        # Seedance produces video-only MP4s when has_dialog=False, so the
        # concat output may have no audio stream. The downstream filter
        # graph references [<shots_audio_input>:a] unconditionally, which
        # would otherwise fail with "matches no streams". Inject a lavfi
        # silent source as the placeholder shots-audio when needed.
        concat_info = probe(concat_path)
        concat_has_audio = any(
            s.get("codec_type") == "audio"
            for s in concat_info.get("streams", []) or []
        )

        # Build the second-pass command
        inputs: list[str] = ["-i", str(concat_path)]  # input 0: concatenated video (+audio)
        idx = 1
        silence_idx: Optional[int] = None
        narration_idx: Optional[int] = None
        music_idx: Optional[int] = None
        title_idx: Optional[int] = None
        end_idx: Optional[int] = None

        if not concat_has_audio:
            inputs += [
                "-f", "lavfi",
                "-t", f"{concat_duration:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
            silence_idx = idx
            idx += 1

        if narration is not None:
            inputs += ["-i", str(narration)]
            narration_idx = idx
            idx += 1
        if music is not None:
            inputs += ["-stream_loop", "-1", "-i", str(music)]  # loop music if shorter
            music_idx = idx
            idx += 1
        if title_card is not None:
            inputs += ["-loop", "1", "-t", f"{title_card_seconds}", "-i", str(title_card)]
            title_idx = idx
            idx += 1
        if end_card is not None:
            inputs += ["-loop", "1", "-t", f"{end_card_seconds}", "-i", str(end_card)]
            end_idx = idx
            idx += 1

        # Build filter_complex
        fc_parts: list[str] = []

        # Video pipeline: shots' video stream, optionally bookended by cards.
        if title_idx is not None or end_idx is not None:
            # Each card normalized to target dims with fade
            card_outs: list[str] = []
            if title_idx is not None:
                fc_parts.append(
                    f"[{title_idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={target_fps},"
                    f"format=yuv420p,fade=t=out:st={title_card_seconds-0.5:.3f}:d=0.5[title_v]"
                )
                card_outs.append("[title_v]")

            fc_parts.append(f"[0:v]setsar=1[shots_v]")
            card_outs.append("[shots_v]")

            if end_idx is not None:
                fc_parts.append(
                    f"[{end_idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={target_fps},"
                    f"format=yuv420p,fade=t=in:st=0:d=0.5[end_v]"
                )
                card_outs.append("[end_v]")

            fc_parts.append(
                f"{''.join(card_outs)}concat=n={len(card_outs)}:v=1:a=0[final_v]"
            )
        else:
            fc_parts.append("[0:v]setsar=1[final_v]")

        # Audio pipeline: start with shot audio (preserves Seedance dialog).
        audio_streams: list[str] = []

        # Shot audio always included at full volume. Use the lavfi silence
        # source as a stand-in when seedance produced video-only shots.
        shots_audio_input = silence_idx if silence_idx is not None else 0
        fc_parts.append(
            f"[{shots_audio_input}:a]asetpts=PTS-STARTPTS,volume=1.0[shots_a]"
        )
        audio_streams.append("[shots_a]")

        # Narration with ducking envelope
        if narration_idx is not None:
            volume_expr = build_ducking_volume_expr(
                base_volume=narration_volume,
                ducked_volume=narration_ducked_volume,
                dialog_windows=dialog_windows,
            )
            # Single-quote the expression: commas inside if(between(t,...))
            # would otherwise be parsed as filter-chain separators.
            fc_parts.append(
                f"[{narration_idx}:a]asetpts=PTS-STARTPTS,"
                f"volume='{volume_expr}':eval=frame[narr_a]"
            )
            audio_streams.append("[narr_a]")

        # Music (looped via -stream_loop, trimmed by amix duration)
        if music_idx is not None:
            fc_parts.append(
                f"[{music_idx}:a]asetpts=PTS-STARTPTS,volume={music_volume}[bgm_a]"
            )
            audio_streams.append("[bgm_a]")

        # Mix all audio streams
        if len(audio_streams) == 1:
            # Only shot audio. amix on a single input is a no-op; skip it.
            fc_parts.append(f"{audio_streams[0]}anull[final_a]")
        else:
            fc_parts.append(
                f"{''.join(audio_streams)}"
                f"amix=inputs={len(audio_streams)}:duration=first:dropout_transition=0[final_a]"
            )

        filter_complex = ";".join(fc_parts)

        cmd = [_FFMPEG, "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[final_v]",
            "-map", "[final_a]",
            "-c:v", "libx264",
            "-preset", DEFAULT_VIDEO_PRESET,
            "-crf", str(DEFAULT_VIDEO_CRF),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", DEFAULT_AUDIO_BITRATE,
            "-ar", str(DEFAULT_AUDIO_SAMPLE_RATE),
            "-movflags", "+faststart",
            str(output),
        ]

        # Timeout: 30s per output second of expected duration
        expected_duration = (
            concat_duration
            + (title_card_seconds if title_idx is not None else 0)
            + (end_card_seconds if end_idx is not None else 0)
        )
        timeout = max(120, min(3600, int(expected_duration * TIMEOUT_PER_OUTPUT_SECOND)))

        _run(cmd, timeout=timeout, description=f"stitch_final -> {output.name}")

    if not output.exists() or output.stat().st_size == 0:
        raise FFmpegError(f"stitch_final produced no output at {output}")


# --------------- helper for orchestrator ---------------

def compute_dialog_windows(
    shots_meta: list[dict],
    *,
    duration_field: str = "duration_seconds",
    has_dialog_field: str = "has_dialog",
    transition_field: str = "transition_to_next",
    transition_overlap_seconds: float = TRANSITION_DURATION_S,
) -> list[tuple[float, float]]:
    """Given an ordered list of shot dicts, compute (start, end) windows on
    the FINAL stitched timeline where has_dialog=True.

    Used by the orchestrator (P11) to pass `dialog_windows` to stitch_final.

    When a shot's transition_field value is 'fade' or 'dissolve', the
    cursor advances by (duration - transition_overlap_seconds) instead of
    full duration, matching the xfade pipeline's overlap accounting.
    Missing transition_field values are treated as hard cuts, so this is
    backward compatible with shots predating the P19-pre migration.
    """
    fade_kinds = {"fade", "dissolve"}
    windows: list[tuple[float, float]] = []
    cursor = 0.0
    last = len(shots_meta) - 1
    for i, shot in enumerate(shots_meta):
        dur = float(shot[duration_field])
        if shot.get(has_dialog_field):
            windows.append((cursor, cursor + dur))
        if i < last and shot.get(transition_field) in fade_kinds:
            cursor += max(0.0, dur - transition_overlap_seconds)
        else:
            cursor += dur
    return windows
