import { useEffect, useState } from 'react';
import type { JSX, ReactNode } from 'react';
import { useStore, selectIsReadOnlyView } from '../../state/store';
import { PhaseCanvas } from './PhaseCanvas';
import { StyleSwatch } from '../widgets/StyleSwatch';
import { presetVisualKey } from '../../lib/styleVisuals';
import { RefDropZone } from '../widgets/RefDropZone';
import { uploadReference, type UploadResult } from '../../lib/uploads';
import { packBrief } from '../../lib/briefEncoding';
import type { Project, StylePreset } from '../../types/sprite';

type Duration = 15 | 30 | 45 | 60 | 75 | 90;
type ChipKey = 'genre' | 'cast' | 'arc' | 'duration';

const DURATIONS: Duration[] = [15, 30, 45, 60, 75, 90];
const GENRE_PRESETS = ['drama', 'comedy', 'noir', 'doc', 'sci-fi'];
const CAST_OPTS: Array<{ v: string; label: string; desc: string }> = [
  { v: '1', label: '1', desc: 'lone fox' },
  { v: '2', label: '2', desc: 'two-hander' },
  { v: '3-4', label: '3-4', desc: 'small cast' },
  { v: 'auto', label: 'auto', desc: 'agent picks' },
];

// Cast size band: matches plugins/sprite-studio/models.py constants.
// Bumps here MUST track MAX_CAST_SIZE / WARN_CAST_SIZE in the Python model.
const MAX_CAST_SIZE = 30;
const WARN_CAST_SIZE = 8;
const HARD_WARN_CAST_SIZE = 12;
const SHEET_COST_USD = 0.21;
const ARC_OPTS: Array<{ v: string; label: string; desc: string }> = [
  { v: 'consistent', label: 'consistent', desc: 'flat affect through' },
  { v: 'twist', label: 'arc + twist', desc: 'set-up to reveal' },
  { v: 'tension', label: 'building', desc: 'slow-burn pressure' },
];

// Computes initial form values from the project (for clarification round-trip
// or mid-flow re-entry). When the lobby's "+ new project" sets a phantom with
// empty id, we treat it as a fresh draft and skip prefill. The component is
// keyed by project id in App.tsx so a different project remounts with fresh
// initial state; no syncing effect needed.
function initialFromProject(project: Project | null): {
  text: string;
  stylePresetId: string;
  duration: Duration;
} {
  if (!project || !project.id) {
    return { text: '', stylePresetId: '', duration: 60 };
  }
  const dur = DURATIONS.includes(project.duration_seconds as Duration)
    ? (project.duration_seconds as Duration)
    : 60;
  return {
    text: project.brief,
    stylePresetId: project.style_preset_id ?? '',
    duration: dur,
  };
}

export function BriefScreen(): JSX.Element {
  const stylePresets = useStore((s) => s.stylePresets);
  const loadStylePresets = useStore((s) => s.loadStylePresets);
  const newProject = useStore((s) => s.newProject);
  const setStyle = useStore((s) => s.setStyle);
  const setDuration = useStore((s) => s.setDuration);
  const setProjectRefs = useStore((s) => s.setProjectRefs);
  const startCast = useStore((s) => s.startCast);
  const project = useStore((s) => s.project);
  const readOnly = useStore(selectIsReadOnlyView);

  const initial = useState(() => initialFromProject(useStore.getState().project))[0];
  const [text, setText] = useState(initial.text);
  const [stylePresetId, setStylePresetId] = useState<string>(initial.stylePresetId);
  const [styleCustom, setStyleCustom] = useState('');
  const [duration, setDurationLocal] = useState<Duration>(initial.duration);
  const [genre, setGenre] = useState('auto');
  const [castSize, setCastSize] = useState('auto');
  const [arcShape, setArcShape] = useState('auto');
  const [openChip, setOpenChip] = useState<ChipKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingRefs, setPendingRefs] = useState<File[]>([]);
  const [uploadStage, setUploadStage] = useState<string | null>(null);

  useEffect(() => {
    if (stylePresets.length === 0) void loadStylePresets();
  }, [stylePresets.length, loadStylePresets]);

  if (readOnly && project) {
    return <ReadOnlyBrief project={project} stylePresets={stylePresets} />;
  }

  const submit = async () => {
    const trimmed = text.trim();
    if (trimmed.length < 5 || busy) return;
    setBusy(true);
    try {
      const customStyle = stylePresetId === 'custom' ? styleCustom : undefined;
      const packed = packBrief(trimmed, { genre, castSize, arcShape, customStyle });
      const haveRefs = pendingRefs.length > 0;

      // With refs we defer the cast advance so the project_id exists,
      // refs upload, /sprite_set_project_refs binds them, then /sprite_cast
      // runs with refs available. Without refs, /sprite_new auto-advances
      // (Fix A: empty-questions ⇒ skip the clarification stall).
      await newProject(packed, { deferCast: haveRefs });
      const proj = useStore.getState().project;
      const pid = proj?.id;

      if (haveRefs && pid) {
        setUploadStage(`uploading 1/${pendingRefs.length}`);
        const uploaded: UploadResult[] = [];
        for (let i = 0; i < pendingRefs.length; i += 1) {
          setUploadStage(`uploading ${i + 1}/${pendingRefs.length}`);
          try {
            const handle = uploadReference(pid, pendingRefs[i]!);
            const result = await handle.promise;
            uploaded.push(result);
          } catch {
            // Per-file failure surfaces in the RefDropZone row already;
            // we keep going so partial-success refs still anchor the cast.
          }
        }
        setUploadStage('binding refs');
        if (uploaded.length > 0) {
          await setProjectRefs(uploaded.map((u) => u.path));
        }
        setUploadStage('generating cast');
        await startCast();
      }

      const after = useStore.getState().project;
      if (!after || after.phase === 'brief') return;
      // Skip setStyle when "custom" is picked. The free text was packed
      // into the brief as [style: ...] for the clarifier to read; the
      // backend rejects unknown preset ids.
      if (stylePresetId && stylePresetId !== 'custom' && stylePresetId !== after.style_preset_id) {
        await setStyle(stylePresetId);
      }
      if (duration !== after.duration_seconds) {
        await setDuration(duration);
      }
    } finally {
      setUploadStage(null);
      setBusy(false);
    }
  };

  const canSubmit = text.trim().length >= 5 && !busy;
  const styleVal = stylePresetId
    ? stylePresets.find((p) => p.id === stylePresetId)?.name ?? stylePresetId
    : 'auto';
  const truncate = (v: string, n = 22) => (v.length > n ? `${v.slice(0, n)}...` : v);

  const compactChip = (k: ChipKey, label: string, val: string) => (
    <button
      type="button"
      onClick={() => setOpenChip(openChip === k ? null : k)}
      className="pill"
      style={{
        cursor: 'pointer',
        padding: '4px 9px',
        fontSize: 10,
        borderStyle: openChip === k ? 'solid' : 'dashed',
        borderColor: openChip === k ? 'var(--accent)' : 'var(--rule-soft)',
        background: 'var(--paper)',
        color: 'var(--ink)',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        maxWidth: 220,
        overflow: 'hidden',
      }}
    >
      <span
        style={{
          color: 'var(--ink-faint)',
          textTransform: 'uppercase',
          letterSpacing: 0.5,
          flex: '0 0 auto',
        }}
      >
        {label}
      </span>
      <span
        className="hand"
        style={{
          fontSize: 13,
          color: 'var(--ink)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: 140,
        }}
      >
        {truncate(val)}
      </span>
      <span style={{ color: 'var(--ink-faint)', fontSize: 9, flex: '0 0 auto' }}>
        {openChip === k ? 'v' : '>'}
      </span>
    </button>
  );

  return (
    <PhaseCanvas phase="brief">
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'grid',
          placeItems: 'center',
          padding: '24px 24px',
          overflowY: 'auto',
        }}
      >
        <div
          className="box-hand"
          style={{
            width: 'min(680px, calc(100% - 32px))',
            maxHeight: 'calc(100% - 16px)',
            overflowY: 'auto',
            padding: '32px 36px',
            background: 'var(--paper)',
            boxSizing: 'border-box',
          }}
        >
          <div className="mono" style={{ fontSize: 9, marginBottom: 10, color: 'var(--ink-faint)' }}>
            01 · NEW BRIEF
          </div>
          <div className="serif-it" style={{ fontSize: 38, lineHeight: 1, marginBottom: 18 }}>
            what's the <span className="underline-hand">scene</span>?
          </div>
          <textarea
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="a quiet two-hander in a rain-soaked diner..."
            style={{ minWidth: 0, width: '100%', boxSizing: 'border-box' }}
          />

          <div style={{ marginTop: 26 }}>
            <div className="mono" style={{ fontSize: 9, marginBottom: 8 }}>
              style preset <span style={{ color: 'var(--accent)' }}>· required</span>
            </div>
            {stylePresets.length === 0 ? (
              <span className="pill" style={{ opacity: 0.6 }}>
                loading styles...
              </span>
            ) : (
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'stretch' }}>
                {stylePresets.map((p) => (
                  <PresetCard
                    key={p.id}
                    preset={p}
                    selected={stylePresetId === p.id}
                    onClick={() => setStylePresetId(p.id)}
                  />
                ))}
                <div
                  onClick={() => setStylePresetId('custom')}
                  className="pressy"
                  style={{
                    cursor: 'pointer',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4,
                    padding: 6,
                    alignItems: 'flex-start',
                    border:
                      stylePresetId === 'custom'
                        ? '1.5px solid var(--accent)'
                        : '1.5px solid transparent',
                    background: stylePresetId === 'custom' ? 'var(--accent-tint)' : 'transparent',
                    borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
                  }}
                >
                  <StyleSwatch visual="custom" size={72} selected={stylePresetId === 'custom'} />
                  <div
                    className="hand"
                    style={{
                      fontSize: 13,
                      color: stylePresetId === 'custom' ? 'var(--accent)' : 'var(--ink-faint)',
                      fontStyle: 'italic',
                    }}
                  >
                    custom...
                  </div>
                </div>
              </div>
            )}
            {stylePresetId === 'custom' && (
              <input
                type="text"
                value={styleCustom}
                onChange={(e) => setStyleCustom(e.target.value)}
                placeholder="e.g. anamorphic · neon haze · 70mm grain"
                style={{ marginTop: 10, minWidth: 0, width: '100%', boxSizing: 'border-box' }}
              />
            )}
          </div>

          <div style={{ marginTop: 26 }}>
            <div className="mono" style={{ fontSize: 9, marginBottom: 8 }}>
              scene tags
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              {compactChip('genre', 'genre', genre)}
              {compactChip('cast', 'cast', castSize)}
              {compactChip('arc', 'tone shift', arcShape)}
              {compactChip('duration', 'duration', `~${duration}s`)}
            </div>

            {openChip === 'genre' && (
              <PickerPanel>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {GENRE_PRESETS.map((g) => (
                    <span
                      key={g}
                      onClick={() => setGenre(g)}
                      className={genre === g ? 'pill pill-accent' : 'pill'}
                      style={{ cursor: 'pointer', padding: '3px 8px' }}
                    >
                      {g}
                    </span>
                  ))}
                  <span
                    onClick={() => setGenre('auto')}
                    className={genre === 'auto' ? 'pill pill-accent' : 'pill'}
                    style={{ cursor: 'pointer', padding: '3px 8px', borderStyle: 'dashed' }}
                  >
                    auto
                  </span>
                </div>
              </PickerPanel>
            )}

            {openChip === 'cast' && (
              <PickerPanel>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                  {CAST_OPTS.map((o) => (
                    <span
                      key={o.v}
                      onClick={() => setCastSize(o.v)}
                      title={o.desc}
                      className={castSize === o.v ? 'pill pill-accent' : 'pill'}
                      style={{ cursor: 'pointer', padding: '3px 8px' }}
                    >
                      {o.label}
                    </span>
                  ))}
                  <span
                    className="mono"
                    style={{ fontSize: 9, color: 'var(--ink-faint)', margin: '0 2px' }}
                  >
                    or
                  </span>
                  <input
                    type="number"
                    min={1}
                    max={MAX_CAST_SIZE}
                    placeholder="N"
                    value={parseCustomCast(castSize) ?? ''}
                    onChange={(e) => {
                      const raw = e.target.value;
                      if (!raw) {
                        setCastSize('auto');
                        return;
                      }
                      const n = Math.max(1, Math.min(MAX_CAST_SIZE, Number(raw)));
                      setCastSize(String(n));
                    }}
                    style={{
                      width: 56,
                      padding: '2px 6px',
                      fontSize: 12,
                      textAlign: 'center',
                    }}
                  />
                </div>
                <CastSizeNote castSize={castSize} />
              </PickerPanel>
            )}

            {openChip === 'arc' && (
              <PickerPanel>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {ARC_OPTS.map((o) => (
                    <span
                      key={o.v}
                      onClick={() => setArcShape(o.v)}
                      title={o.desc}
                      className={arcShape === o.v ? 'pill pill-accent' : 'pill'}
                      style={{ cursor: 'pointer', padding: '3px 8px' }}
                    >
                      {o.label}
                    </span>
                  ))}
                  <span
                    onClick={() => setArcShape('auto')}
                    className={arcShape === 'auto' ? 'pill pill-accent' : 'pill'}
                    style={{ cursor: 'pointer', padding: '3px 8px', borderStyle: 'dashed' }}
                  >
                    auto
                  </span>
                </div>
                <div className="hand" style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
                  {ARC_OPTS.find((o) => o.v === arcShape)?.desc ?? 'agent picks the shape'}
                </div>
              </PickerPanel>
            )}

            {openChip === 'duration' && (
              <PickerPanel>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {DURATIONS.map((d) => (
                    <span
                      key={d}
                      onClick={() => setDurationLocal(d)}
                      className={duration === d ? 'pill pill-accent' : 'pill'}
                      style={{ cursor: 'pointer', padding: '3px 8px' }}
                    >
                      {d}s
                    </span>
                  ))}
                </div>
                <div className="hand" style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
                  shot count scales with duration; 60s ≈ 6 shots.
                </div>
              </PickerPanel>
            )}
          </div>

          <div style={{ marginTop: 14 }}>
            <div
              className="mono"
              style={{
                fontSize: 9,
                marginBottom: 4,
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span>ref images</span>
              <span style={{ color: 'var(--ink-faint)' }}>{pendingRefs.length}/8 · png/jpeg/webp</span>
            </div>
            <RefDropZone
              projectId={null}
              onPendingChange={setPendingRefs}
              max={8}
            />
          </div>

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              marginTop: 16,
            }}
          >
            <span style={{ flex: 1 }} />
            <span className="mono" style={{ color: 'var(--ink-faint)' }}>
              ~{duration}s · estimated
            </span>
            <button
              className="cta"
              onClick={() => void submit()}
              disabled={!canSubmit}
              style={{ opacity: canSubmit ? 1 : 0.5, cursor: canSubmit ? 'pointer' : 'not-allowed' }}
            >
              {busy ? (uploadStage ?? 'casting...') : 'cast it'} <span style={{ fontSize: 13 }}>▸</span>
            </button>
          </div>

          <div
            style={{
              borderTop: '1px dashed var(--rule-soft)',
              marginTop: 14,
              paddingTop: 8,
              overflow: 'hidden',
            }}
          >
            <span
              className="mono"
              style={{
                color: 'var(--ink-faint)',
                fontSize: 10,
                display: 'block',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              | /sprite_new "{text.slice(0, 28)}..." style={styleVal} dur={duration}s
            </span>
          </div>
        </div>
      </div>
    </PhaseCanvas>
  );
}

// Past-phase view of the brief: static recap of text + style/duration/vibe,
// no inputs, no submit. Mounted by BriefScreen when isReadOnlyView is true.
function ReadOnlyBrief({
  project,
  stylePresets,
}: {
  project: Project;
  stylePresets: StylePreset[];
}): JSX.Element {
  const styleName =
    stylePresets.find((p) => p.id === project.style_preset_id)?.name
    ?? project.style_preset_id
    ?? 'auto';
  return (
    <PhaseCanvas phase="brief">
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'grid',
          placeItems: 'center',
          padding: '24px',
          overflowY: 'auto',
        }}
      >
        <div
          className="box-hand"
          style={{
            width: 'min(680px, calc(100% - 32px))',
            maxHeight: 'calc(100% - 16px)',
            overflowY: 'auto',
            padding: '32px 36px',
            background: 'var(--paper)',
            boxSizing: 'border-box',
          }}
        >
          <div
            className="mono"
            style={{ fontSize: 9, marginBottom: 10, color: 'var(--ink-faint)' }}
          >
            01 · ORIGINAL BRIEF
          </div>
          <div
            className="serif-it"
            style={{ fontSize: 38, lineHeight: 1, marginBottom: 18 }}
          >
            the <span className="underline-hand">scene</span>.
          </div>
          <div
            className="hand"
            style={{
              fontSize: 17,
              lineHeight: 1.45,
              color: 'var(--ink)',
              whiteSpace: 'pre-wrap',
              padding: '12px 14px',
              border: '1.5px dashed var(--rule-soft)',
              background: 'var(--paper-tint)',
              borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
            }}
          >
            {project.brief}
          </div>

          <div
            style={{
              marginTop: 22,
              display: 'flex',
              gap: 10,
              flexWrap: 'wrap',
            }}
          >
            <ReadOnlyTag label="style" value={styleName} />
            <ReadOnlyTag label="duration" value={`${project.duration_seconds}s`} />
            {project.vibe && <ReadOnlyTag label="vibe" value={project.vibe} />}
          </div>
        </div>
      </div>
    </PhaseCanvas>
  );
}

function ReadOnlyTag({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <span
      className="pill"
      style={{
        padding: '4px 10px',
        fontSize: 10,
        borderStyle: 'solid',
        borderColor: 'var(--rule-soft)',
        background: 'var(--paper)',
        color: 'var(--ink)',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
      }}
    >
      <span
        style={{
          color: 'var(--ink-faint)',
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}
      >
        {label}
      </span>
      <span className="hand" style={{ fontSize: 13 }}>
        {value}
      </span>
    </span>
  );
}

interface PresetCardProps {
  preset: StylePreset;
  selected: boolean;
  onClick: () => void;
}

function PresetCard({ preset, selected, onClick }: PresetCardProps): JSX.Element {
  const visual = presetVisualKey(preset);
  return (
    <div
      onClick={onClick}
      className="pressy"
      style={{
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        padding: 6,
        alignItems: 'flex-start',
        border: selected ? '1.5px solid var(--accent)' : '1.5px solid transparent',
        background: selected ? 'var(--accent-tint)' : 'transparent',
        borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
      }}
    >
      <StyleSwatch visual={visual} size={72} selected={selected} />
      <div style={{ lineHeight: 1.05, maxWidth: 84 }}>
        <div
          className="hand"
          style={{
            fontSize: 13,
            color: selected ? 'var(--accent)' : 'var(--ink)',
            fontWeight: 600,
          }}
        >
          {preset.name}
        </div>
        {preset.descriptor && (
          <div
            className="mono"
            style={{
              fontSize: 8,
              color: 'var(--ink-faint)',
              marginTop: 1,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {preset.descriptor.slice(0, 60)}
          </div>
        )}
      </div>
    </div>
  );
}

interface PanelProps {
  children: ReactNode;
}

function parseCustomCast(value: string): number | null {
  if (!value) return null;
  if (CAST_OPTS.some((o) => o.v === value)) return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 1) return null;
  return Math.floor(n);
}

function CastSizeNote({ castSize }: { castSize: string }): JSX.Element {
  const custom = parseCustomCast(castSize);
  if (custom === null) {
    const desc = CAST_OPTS.find((o) => o.v === castSize)?.desc ?? '';
    return (
      <div className="hand" style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
        {desc}
      </div>
    );
  }
  const sheetEst = (custom * SHEET_COST_USD).toFixed(2);
  if (custom > HARD_WARN_CAST_SIZE) {
    return (
      <div
        className="hand"
        style={{ fontSize: 11, color: 'var(--accent)', marginTop: 4 }}
      >
        large cast (~${sheetEst} for sheets); /sprite_approve_cast_size required
        before /sprite_cast spends image budget.
      </div>
    );
  }
  if (custom > WARN_CAST_SIZE) {
    return (
      <div
        className="hand"
        style={{ fontSize: 11, color: 'var(--accent)', marginTop: 4 }}
      >
        large cast: ~${sheetEst} expected for sheet generation alone.
      </div>
    );
  }
  return (
    <div className="hand" style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
      custom cast size · ~${sheetEst} for sheets
    </div>
  );
}

function PickerPanel({ children }: PanelProps): JSX.Element {
  return (
    <div
      style={{
        marginTop: 8,
        padding: 10,
        border: '1.5px dashed var(--rule-soft)',
        borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
        background: 'var(--bg)',
      }}
    >
      {children}
    </div>
  );
}
