import { useEffect } from 'react';
import type { JSX } from 'react';
import { useStore, selectIsReadOnlyView } from '../../state/store';
import { PhaseCanvas } from './PhaseCanvas';
import { ShotStill } from '../sprites/ShotStill';
import { kindForShot } from '../../lib/design';
import { shotReferenceUrl } from '../../lib/assets';

function fmtEta(seconds: number | null | undefined): string {
  if (seconds == null) return '-';
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function RenderScreen(): JSX.Element {
  const project = useStore((s) => s.project);
  const shots = useStore((s) => s.shots);
  const status = useStore((s) => s.status);
  const startProgressPolling = useStore((s) => s.startProgressPolling);
  const stopProgressPolling = useStore((s) => s.stopProgressPolling);
  const cancelRender = useStore((s) => s.cancelRender);
  const readOnly = useStore(selectIsReadOnlyView);

  useEffect(() => {
    // Past-phase view is a snapshot; polling would burn cycles for nothing
    // and could re-hydrate the screen with stale data mid-inspection.
    if (readOnly) return;
    startProgressPolling(3000);
    return () => stopProgressPolling();
  }, [readOnly, startProgressPolling, stopProgressPolling]);

  if (!project) {
    return (
      <PhaseCanvas phase="render">
        <div />
      </PhaseCanvas>
    );
  }

  const projectId = project.id;
  const projectStatus = status?.project ?? null;
  const totalShots = shots.length;
  const doneShots = shots.filter((s) => s.render_status === 'done').length;
  const cost = projectStatus?.total_cost_usd ?? project.total_cost_usd ?? 0;
  const eta = projectStatus?.eta_seconds ?? null;
  const stage = projectStatus?.current_step ?? 'idle';
  const detail = projectStatus?.progress_detail ?? '';

  return (
    <PhaseCanvas phase="render">
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'grid',
          gridTemplateColumns: '1fr 320px',
          gap: 24,
          padding: '28px 32px',
          overflow: 'auto',
        }}
      >
        <div style={{ minWidth: 0, overflow: 'auto' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              gap: 12,
              marginBottom: 18,
              flexWrap: 'wrap',
            }}
          >
            <h1 className="serif-it" style={{ fontSize: 48, margin: 0, lineHeight: 1 }}>
              {readOnly ? 'render snapshot' : 'rendering'}
              <span className="accent">{readOnly ? '.' : '…'}</span>
            </h1>
            {!readOnly && (
              <>
                <span className="pill pill-accent">{`○ ${stage}`}</span>
                {eta != null && <span className="pill">{`ETA ${fmtEta(eta)}`}</span>}
              </>
            )}
            {readOnly && (
              <span className="pill" style={{ borderStyle: 'solid' }}>
                {`${doneShots}/${totalShots} shots · final`}
              </span>
            )}
          </div>

          {totalShots === 0 ? (
            <div className="sticky-note" style={{ display: 'inline-block', maxWidth: 360 }}>
              no shots in this project. /sprite_render had nothing to send.
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 12, alignItems: 'stretch', flexWrap: 'wrap' }}>
              {shots.map((s, i) => {
                const done = s.render_status === 'done';
                const rendering = s.render_status === 'rendering';
                const failed = s.render_status === 'failed';
                const w = 80 + s.duration_seconds * 9;
                const bg = done
                  ? 'rgba(79,122,76,0.10)'
                  : rendering
                    ? 'var(--accent-tint)'
                    : failed
                      ? 'var(--accent-tint-strong)'
                      : 'var(--paper)';
                const border = done
                  ? '1.5px solid var(--good)'
                  : rendering
                    ? '1.5px solid var(--accent)'
                    : failed
                      ? '1.5px solid var(--accent)'
                      : '1.5px dashed var(--rule-soft)';
                const hasReal = Boolean(s.reference_still_path);
                const dim = !done && !rendering && !failed;
                return (
                  <div
                    key={s.id}
                    style={{
                      width: w,
                      padding: 8,
                      background: bg,
                      border,
                      borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
                      position: 'relative',
                      opacity: dim ? 0.6 : 1,
                    }}
                  >
                    <div
                      style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}
                    >
                      <span
                        className="mono"
                        style={{
                          fontSize: 8,
                          color: rendering ? 'var(--accent)' : 'var(--ink-soft)',
                        }}
                      >
                        {String(i + 1).padStart(2, '0')}
                      </span>
                      {done && (
                        <span
                          style={{
                            width: 16,
                            height: 16,
                            borderRadius: '50%',
                            background: 'var(--good)',
                            color: 'var(--paper)',
                            fontSize: 10,
                            display: 'grid',
                            placeItems: 'center',
                            fontFamily: 'var(--mono)',
                          }}
                        >
                          ✓
                        </span>
                      )}
                      {failed && (
                        <span
                          style={{
                            width: 16,
                            height: 16,
                            borderRadius: '50%',
                            background: 'var(--accent)',
                            color: 'var(--paper)',
                            fontSize: 10,
                            display: 'grid',
                            placeItems: 'center',
                            fontFamily: 'var(--mono)',
                          }}
                        >
                          ✕
                        </span>
                      )}
                    </div>
                    {hasReal ? (
                      <img
                        src={shotReferenceUrl(projectId, s.id, s.updated_at ?? undefined)}
                        alt={`shot ${i + 1}`}
                        style={{
                          width: w - 16,
                          height: 50,
                          objectFit: 'cover',
                          display: 'block',
                          border: '1px solid var(--rule-soft)',
                        }}
                      />
                    ) : (
                      <ShotStill
                        kind={kindForShot({
                          characters_present: s.characters_present,
                          camera: s.camera,
                        })}
                        size={{ w: w - 16, h: 50 }}
                        dim={dim}
                      />
                    )}
                    <div
                      className="mono"
                      style={{ fontSize: 8, marginTop: 4, color: 'var(--ink-faint)' }}
                    >
                      {`${done ? 'done' : rendering ? 'rendering' : failed ? 'failed' : 'pending'} · ${s.duration_seconds}s`}
                    </div>
                    {rendering && (
                      <div
                        style={{
                          marginTop: 4,
                          height: 3,
                          background: 'var(--paper-tint)',
                          borderRadius: 2,
                          overflow: 'hidden',
                        }}
                      >
                        <div
                          style={{
                            width: '60%',
                            height: '100%',
                            background: 'var(--accent)',
                            transition: 'width 200ms linear',
                          }}
                        />
                      </div>
                    )}
                    {failed && s.render_error && (
                      <div
                        className="mono"
                        style={{
                          fontSize: 7,
                          color: 'var(--accent)',
                          marginTop: 2,
                          lineHeight: 1.2,
                        }}
                      >
                        {s.render_error.slice(0, 60)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {detail && !readOnly && (
            <div
              className="hand"
              style={{ fontSize: 14, color: 'var(--ink-soft)', marginTop: 14 }}
            >
              {detail}
            </div>
          )}

          {!readOnly && (
            <div
              className="box-hand"
              style={{ marginTop: 20, padding: 12, fontFamily: 'var(--mono)', fontSize: 10 }}
            >
              <div
                style={{
                  fontSize: 9,
                  color: 'var(--ink-faint)',
                  marginBottom: 6,
                  letterSpacing: '0.12em',
                }}
              >
                LIVE LOG
              </div>
              <div style={{ lineHeight: 1.5, color: 'var(--ink-soft)' }}>
                <LogLines stage={stage} detail={detail} doneShots={doneShots} totalShots={totalShots} />
              </div>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div className="mono" style={{ fontSize: 9, marginBottom: 6 }}>
              STATUS
            </div>
            <div className="serif-it" style={{ fontSize: 28, color: 'var(--accent)' }}>
              {!readOnly && <span className="pulsing-dot" style={{ marginRight: 8 }} />}
              {readOnly ? (project.phase === 'failed' ? 'failed' : 'done') : stage}
            </div>
          </div>

          <div className="box-hand" style={{ padding: 14 }}>
            <div className="mono" style={{ fontSize: 9, marginBottom: 4 }}>
              progress
            </div>
            <div className="serif-it" style={{ fontSize: 36, lineHeight: 1 }}>
              {`${doneShots}/${totalShots}`}{' '}
              <span className="hand" style={{ fontSize: 16, color: 'var(--ink-soft)' }}>
                shots
              </span>
            </div>
            <div
              style={{
                marginTop: 10,
                height: 6,
                background: 'var(--paper-tint)',
                borderRadius: 3,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${totalShots > 0 ? (doneShots / totalShots) * 100 : 0}%`,
                  height: '100%',
                  background: 'var(--accent)',
                  transition: 'width 200ms linear',
                }}
              />
            </div>
          </div>

          {!readOnly && (
            <button
              className="cta cta-ghost"
              onClick={() => void cancelRender()}
              style={{ padding: '10px 14px' }}
            >
              ✕ cancel
            </button>
          )}

          <div className="box-hand" style={{ padding: 14 }}>
            <div className="mono" style={{ fontSize: 9, marginBottom: 4 }}>
              {readOnly ? 'cost · final' : 'cost · live'}
            </div>
            <div className="serif-it" style={{ fontSize: 32, lineHeight: 1 }}>
              {`$${cost.toFixed(2)}`}
            </div>
            {!readOnly && eta != null && (
              <div
                className="mono"
                style={{ fontSize: 9, color: 'var(--ink-faint)', marginTop: 4 }}
              >
                {`ETA ${fmtEta(eta)}`}
              </div>
            )}
          </div>

          {!readOnly && (
            <div className="sticky-note">
              tip: cancel keeps shots already done.
              <br />
              re-run /sprite_render to resume.
            </div>
          )}
        </div>
      </div>
    </PhaseCanvas>
  );
}

interface LogLinesProps {
  stage: string;
  detail: string;
  doneShots: number;
  totalShots: number;
}

// The backend's /sprite_status response only carries the current step and
// detail; it does not return a rolling event log. We synthesize the last
// few entries from the per-shot render_status counts plus the live stage
// line, which is enough for the user to see motion in the panel.
function LogLines({ stage, detail, doneShots, totalShots }: LogLinesProps): JSX.Element {
  const lines: { text: string; tone: 'soft' | 'accent' | 'faint' }[] = [];
  for (let i = 1; i <= Math.min(doneShots, 4); i += 1) {
    lines.push({ text: `✓ shot ${i} done`, tone: 'soft' });
  }
  if (doneShots < totalShots) {
    lines.push({
      text: `○ shot ${doneShots + 1} · ${detail || stage}`,
      tone: 'accent',
    });
    if (doneShots + 2 <= totalShots) {
      lines.push({ text: `[ - ] shot ${doneShots + 2} queued`, tone: 'faint' });
    }
  } else if (totalShots > 0) {
    lines.push({ text: `○ ${stage}`, tone: 'accent' });
  } else {
    lines.push({ text: `○ ${stage}`, tone: 'soft' });
  }
  const trimmed = lines.slice(-6);
  return (
    <>
      {trimmed.map((line, i) => (
        <div
          key={i}
          style={{
            color:
              line.tone === 'accent'
                ? 'var(--accent)'
                : line.tone === 'faint'
                  ? 'var(--ink-faint)'
                  : 'var(--ink-soft)',
          }}
        >
          {line.text}
        </div>
      ))}
    </>
  );
}
