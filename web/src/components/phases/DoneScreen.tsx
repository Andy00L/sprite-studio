import type { JSX } from 'react';
import { useStore } from '../../state/store';
import { PhaseCanvas } from './PhaseCanvas';
import { projectFinalVideoUrl } from '../../lib/assets';

export function DoneScreen(): JSX.Element {
  const project = useStore((s) => s.project);
  const characters = useStore((s) => s.characters);
  const shots = useStore((s) => s.shots);
  const sendRaw = useStore((s) => s.sendRaw);

  if (!project) {
    return (
      <PhaseCanvas phase="done">
        <div />
      </PhaseCanvas>
    );
  }

  const totalDur = shots.reduce((s, x) => s + x.duration_seconds, 0);
  const isFailed = project.phase === 'failed';
  const videoUrl = project.final_video_path
    ? projectFinalVideoUrl(project.id, project.updated_at)
    : null;

  return (
    <PhaseCanvas phase={project.phase}>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'grid',
          placeItems: 'center',
          padding: 32,
          overflow: 'auto',
        }}
      >
        <div style={{ textAlign: 'center', maxWidth: 720 }}>
          <div
            className="mono"
            style={{
              fontSize: 9,
              marginBottom: 8,
              color: isFailed ? 'var(--accent)' : 'var(--good)',
            }}
          >
            ● {isFailed ? 'FAILED' : 'DONE'} · {project.final_video_path ? 'final.mp4' : 'no output'}
          </div>
          <h1 className="serif-it" style={{ fontSize: 64, margin: 0, lineHeight: 1 }}>
            {isFailed ? 'render failed' : 'all done'}
            <span className="accent">.</span>
          </h1>
          <div className="hand" style={{ fontSize: 18, color: 'var(--ink-soft)', marginTop: 8 }}>
            {shots.length} shot{shots.length === 1 ? '' : 's'} · {totalDur}s · $
            {project.total_cost_usd.toFixed(2)}
          </div>

          {videoUrl && (
            <div
              className="box-hand"
              style={{
                marginTop: 22,
                padding: 14,
                display: 'inline-block',
                background: 'var(--paper)',
              }}
            >
              <video
                src={videoUrl}
                controls
                preload="metadata"
                style={{
                  width: 540,
                  maxWidth: '100%',
                  height: 'auto',
                  display: 'block',
                  border: '1.5px solid var(--rule)',
                }}
              />
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-around',
                  marginTop: 14,
                  gap: 18,
                  flexWrap: 'wrap',
                }}
              >
                <Stat label="duration" value={`${totalDur}s`} />
                <Stat label="characters" value={String(characters.length)} />
                <Stat label="shots" value={String(shots.length)} />
                <Stat label="cost" value={`$${project.total_cost_usd.toFixed(2)}`} />
              </div>
            </div>
          )}

          {isFailed && (
            <div
              className="sticky-note"
              style={{ display: 'inline-block', marginTop: 20, maxWidth: 540 }}
            >
              {project.error_message ?? 'unknown error'}
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 22 }}>
            {videoUrl && (
              <a
                href={videoUrl}
                download
                className="cta cta-ghost"
                style={{ textDecoration: 'none' }}
              >
                ↓ download
              </a>
            )}
            <button
              className="cta cta-ghost"
              onClick={() => {
                // A failed project with no shots crashed at or before the
                // timeline-gen stage (often the orphan-recovery sweep), so
                // /sprite_timeline is the right retry. With shots present,
                // it's a render-stage failure and /sprite_render resumes.
                const cmd =
                  isFailed && shots.length === 0
                    ? '/sprite_timeline'
                    : '/sprite_render';
                void sendRaw(cmd);
              }}
            >
              {isFailed ? '↻ retry' : '↻ remix'}
            </button>
          </div>
        </div>
      </div>
    </PhaseCanvas>
  );
}

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <div className="mono" style={{ fontSize: 8 }}>
        {label}
      </div>
      <div className="serif-it" style={{ fontSize: 22 }}>
        {value}
      </div>
    </div>
  );
}
