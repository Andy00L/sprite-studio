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
  // Mirror the backend [^A-Za-z0-9._-]+ → _ sanitization so the suggested
  // filename matches what the server will set in Content-Disposition.
  const downloadStem =
    (project.title || 'sprite-studio-video')
      .replace(/[^A-Za-z0-9._-]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'sprite-studio-video';
  const downloadFilename = `${downloadStem}.mp4`;
  const downloadUrl = project.final_video_path
    ? projectFinalVideoUrl(project.id, project.updated_at, {
        download: true,
        filename: downloadFilename,
      })
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
                maxWidth: 540,
              }}
            >
              {/*
                Cap height so the full DoneScreen stack fits without
                scrolling. The 400px subtracts non-video chrome:
                  ~32 outer top padding + ~20 status line + ~64 h1 +
                  ~30 subtitle + ~22 box margin + ~28 box padding y +
                  ~64 stats grid + ~70 buttons row + ~32 outer bottom +
                  ~40 chat dock + ~18 slack.
                width:auto + object-fit:contain preserves aspect ratio
                for both 9:16 and future 16:9 outputs.
              */}
              <video
                src={videoUrl}
                controls
                preload="metadata"
                style={{
                  maxWidth: '100%',
                  maxHeight: 'calc(100vh - 400px)',
                  width: 'auto',
                  height: 'auto',
                  objectFit: 'contain',
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
            {downloadUrl && (
              <a
                href={downloadUrl}
                download={downloadFilename}
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
