import { useState } from 'react';
import type { JSX } from 'react';
import { Backdrop } from '../chrome/Backdrop';
import { useStore } from '../../state/store';
import { TRANSITIONS } from '../../lib/constraints';
import type { ShotTransition } from '../../types/sprite';

interface Props {
  shotId: string;
  current: ShotTransition;
  onClose: () => void;
}

const DESCRIPTIONS: Record<ShotTransition, string> = {
  cut: 'hard splice. no overlap.',
  fade: 'fade through paper.',
  dissolve: 'cross-blend the two shots.',
  match_cut: 'cut on a matching shape.',
};

export function TransitionPopover({
  shotId,
  current,
  onClose,
}: Props): JSX.Element {
  const setShotTransition = useStore((s) => s.setShotTransition);
  const [picked, setPicked] = useState<ShotTransition>(current);
  const [busy, setBusy] = useState(false);

  const save = async (): Promise<void> => {
    if (picked === current) {
      onClose();
      return;
    }
    setBusy(true);
    try {
      await setShotTransition(shotId, picked);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Backdrop onClose={onClose}>
      <div
        className="popover"
        style={{ width: 'min(420px, 100%)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <button className="popover-close" onClick={onClose} aria-label="close">
          ✕
        </button>

        <div style={{ marginBottom: 14, paddingRight: 32 }}>
          <div className="mono" style={{ fontSize: 9, marginBottom: 4 }}>
            edit · transition
          </div>
          <div className="serif-it" style={{ fontSize: 28, lineHeight: 1 }}>
            {picked}
            <span style={{ color: 'var(--accent)' }}>.</span>
          </div>
          <div className="mono" style={{ fontSize: 9, marginTop: 6 }}>
            between this shot and the next
          </div>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 8,
            marginBottom: 14,
          }}
        >
          {TRANSITIONS.map((t) => {
            const active = picked === t;
            return (
              <div
                key={t}
                onClick={() => setPicked(t)}
                style={{
                  padding: '10px 12px',
                  cursor: 'pointer',
                  border: active
                    ? '1.5px solid var(--accent)'
                    : '1.5px dashed var(--rule-soft)',
                  background: active ? 'var(--accent-tint)' : 'transparent',
                  borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
                }}
              >
                <div
                  className="serif-it"
                  style={{
                    fontSize: 18,
                    lineHeight: 1,
                    color: active ? 'var(--accent)' : 'var(--ink)',
                  }}
                >
                  ↦ {t}
                </div>
                <div
                  className="hand"
                  style={{
                    fontSize: 12,
                    color: 'var(--ink-soft)',
                    marginTop: 4,
                  }}
                >
                  {DESCRIPTIONS[t]}
                </div>
              </div>
            );
          })}
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
          }}
        >
          <button
            onClick={onClose}
            disabled={busy}
            className="cta cta-ghost"
          >
            cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={busy}
            className="cta"
          >
            {busy ? '…' : '✓ apply'}
          </button>
        </div>
      </div>
    </Backdrop>
  );
}
