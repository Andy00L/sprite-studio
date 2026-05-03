import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import {
  useStore,
  selectEffectivePhase,
  selectCanNavigatePast,
  selectIsReadOnlyView,
} from '../../state/store';
import type { ProjectPhase } from '../../types/sprite';

const HEADER_PHASES: ProjectPhase[] = ['brief', 'cast', 'timeline', 'render', 'done'];

const ADVANCE_LABEL: Record<ProjectPhase, string> = {
  brief: 'cast it',
  cast: 'approve cast',
  timeline: 'render',
  render: 'open output',
  done: 'remix',
  failed: 'retry',
};

interface Props {
  onBack?: () => void;
  onAdvance?: () => void;
}

export function Header({ onBack, onAdvance }: Props): JSX.Element {
  const project = useStore((s) => s.project);
  const assetServerUp = useStore((s) => s.assetServerUp);
  const effectivePhase = useStore(selectEffectivePhase);
  const canNavigatePast = useStore(selectCanNavigatePast);
  const readOnly = useStore(selectIsReadOnlyView);
  const characters = useStore((s) => s.characters);
  const shots = useStore((s) => s.shots);
  const setViewedPhase = useStore((s) => s.setViewedPhase);
  const [bridgeUp, setBridgeUp] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await fetch('/api/health');
        if (!cancelled) setBridgeUp(r.ok);
      } catch {
        if (!cancelled) setBridgeUp(false);
      }
    };
    void check();
    const timer = setInterval(() => void check(), 15_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const phase: ProjectPhase | null = project ? effectivePhase : null;
  const livePhase = project?.phase ?? null;
  const projectName = project?.title ?? project?.brief?.slice(0, 40) ?? '';
  const cost = `$${(project?.total_cost_usd ?? 0).toFixed(2)}`;

  // Past-phase reachability. On a done project every prior phase has data;
  // on a failed project we gate by the records that actually exist so a
  // crash before timeline-gen doesn't pretend the timeline node is browsable.
  const isReachable = (p: ProjectPhase): boolean => {
    if (!canNavigatePast) return p === livePhase;
    if (livePhase === 'done') return true;
    if (p === 'brief') return true;
    if (p === 'cast') return characters.length > 0;
    if (p === 'timeline') return shots.length > 0;
    if (p === 'render') return shots.some((x) => Boolean(x.rendered_video_path));
    return false;
  };

  return (
    <header
      style={{
        flex: '0 0 auto',
        borderBottom: '1.5px solid var(--rule)',
        padding: '12px 22px',
        display: 'grid',
        gridTemplateColumns: 'minmax(0, auto) minmax(0, 1fr) minmax(0, auto)',
        alignItems: 'center',
        gap: 24,
        background: 'var(--paper)',
        position: 'relative',
        zIndex: 5,
        minHeight: 56,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          whiteSpace: 'nowrap',
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        <button
          className="mono"
          onClick={onBack}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--ink-soft)',
            padding: 0,
            whiteSpace: 'nowrap',
            flex: '0 0 auto',
          }}
        >
          ← all projects
        </button>
        <span style={{ color: 'var(--ink-faint)', flex: '0 0 auto' }}>|</span>
        <span
          style={{
            fontFamily: 'var(--serif)',
            fontStyle: 'italic',
            fontSize: 22,
            color: 'var(--ink)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            minWidth: 0,
          }}
        >
          {projectName || 'no project'}
        </span>
        {phase && (
          <span
            className="pill pill-faint"
            style={{
              borderColor: 'var(--accent)',
              color: 'var(--accent)',
              borderStyle: 'solid',
              flex: '0 0 auto',
            }}
          >
            <span className="pulsing-dot" style={{ width: 6, height: 6 }} /> phase · {phase}
          </span>
        )}
        {readOnly && (
          <span
            className="pill"
            title='click "done" to return to the final render'
            style={{
              borderColor: 'var(--accent)',
              borderStyle: 'dashed',
              background: 'var(--accent-tint)',
              color: 'var(--accent)',
              flex: '0 0 auto',
              fontSize: 9,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
            }}
          >
            ◷ read-only
          </span>
        )}
      </div>
      <div
        style={{
          display: 'flex',
          gap: 10,
          alignItems: 'center',
          justifyContent: 'center',
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        {HEADER_PHASES.map((p, i) => {
          const reachable = isReachable(p);
          const active = p === phase;
          // step-chip handles the live cursor/hover styling; here we only
          // override interactivity. The button is the same visual chip for
          // pre-terminal projects (canNavigatePast=false) where it has no
          // effect: onClick early-returns when reachable is false.
          return (
            <span key={p} style={{ display: 'inline-flex', alignItems: 'center' }}>
              <button
                type="button"
                className={`step-chip ${active ? 'active' : ''}`}
                onClick={() => {
                  if (!canNavigatePast || !reachable) return;
                  setViewedPhase(p);
                }}
                disabled={!canNavigatePast || !reachable}
                aria-current={active ? 'step' : undefined}
                style={{
                  whiteSpace: 'nowrap',
                  background: 'transparent',
                  border: 'none',
                  padding: 0,
                  font: 'inherit',
                  letterSpacing: 'inherit',
                  textTransform: 'inherit',
                  cursor:
                    canNavigatePast && reachable
                      ? 'pointer'
                      : canNavigatePast
                        ? 'not-allowed'
                        : 'default',
                  opacity: canNavigatePast && !reachable ? 0.45 : 1,
                }}
              >
                {p}
              </button>
              {i < HEADER_PHASES.length - 1 && (
                <span
                  style={{
                    color: 'var(--ink-faint)',
                    fontFamily: 'var(--mono)',
                    fontSize: 9,
                    margin: '0 6px',
                  }}
                >
                  ·
                </span>
              )}
            </span>
          );
        })}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, whiteSpace: 'nowrap' }}>
        <span className="mono">
          cost · <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{cost}</span>
        </span>
        <span
          className="pill pill-faint"
          title="Bridge sidecar at /api"
          style={{
            borderColor: bridgeUp ? 'var(--good)' : 'var(--accent)',
            color: bridgeUp ? 'var(--good)' : 'var(--accent)',
          }}
        >
          ● bridge
        </span>
        <span
          className="pill pill-faint"
          title="Asset server at :9120"
          style={{
            borderColor: assetServerUp ? 'var(--good)' : 'var(--accent)',
            color: assetServerUp ? 'var(--good)' : 'var(--accent)',
          }}
        >
          ● assets
        </span>
        {livePhase && !readOnly && (
          <button className="cta" onClick={onAdvance} disabled={!onAdvance}>
            {ADVANCE_LABEL[livePhase]} <span style={{ fontSize: 13 }}>▸</span>
          </button>
        )}
      </div>
    </header>
  );
}
