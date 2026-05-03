import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { useStore } from '../../state/store';
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
  onJumpPhase?: (p: ProjectPhase) => void;
}

export function Header({ onBack, onAdvance, onJumpPhase }: Props): JSX.Element {
  const project = useStore((s) => s.project);
  const assetServerUp = useStore((s) => s.assetServerUp);
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

  const phase: ProjectPhase | null = project?.phase ?? null;
  const projectName = project?.title ?? project?.brief?.slice(0, 40) ?? '';
  const cost = `$${(project?.total_cost_usd ?? 0).toFixed(2)}`;

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
        {HEADER_PHASES.map((p, i) => (
          <span key={p} style={{ display: 'inline-flex', alignItems: 'center' }}>
            <span
              className={`step-chip ${p === phase ? 'active' : ''}`}
              onClick={() => onJumpPhase?.(p)}
              style={{ whiteSpace: 'nowrap' }}
            >
              {p}
            </span>
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
        ))}
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
        {phase && (
          <button className="cta" onClick={onAdvance} disabled={!onAdvance}>
            {ADVANCE_LABEL[phase]} <span style={{ fontSize: 13 }}>▸</span>
          </button>
        )}
      </div>
    </header>
  );
}
