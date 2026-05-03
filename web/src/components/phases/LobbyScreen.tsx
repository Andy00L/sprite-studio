import { useEffect, useState } from 'react';
import type { JSX, MouseEvent } from 'react';
import { useStore, type LobbyFilter } from '../../state/store';
import type { Project, ProjectListEntry } from '../../types/sprite';
import { ShotStill } from '../sprites/ShotStill';
import { kindForShot } from '../../lib/design';
import { assetBase } from '../../lib/assets';

const FILTERS: LobbyFilter[] = ['all', 'in-flight', 'done', 'drafts'];

export function LobbyScreen(): JSX.Element {
  const projects = useStore((s) => s.projects);
  const loadProjects = useStore((s) => s.loadProjects);
  const openProject = useStore((s) => s.openProject);
  const setActiveProject = useStore((s) => s.setActiveProject);
  const [filter, setFilter] = useState<LobbyFilter>('all');

  useEffect(() => {
    void loadProjects(filter);
  }, [filter, loadProjects]);

  // Refresh every 10s so projects started from CLI surface in this tab
  // without a manual reload. Cleanup runs on unmount or filter change.
  useEffect(() => {
    const id = setInterval(() => void loadProjects(filter), 10_000);
    return () => clearInterval(id);
  }, [filter, loadProjects]);

  const startNew = () => {
    setActiveProject(null);
    // Phantom brief-phase project routes App.tsx to BriefScreen. The empty
    // id signals "fresh draft"; BriefScreen's prefill effect skips when
    // id is falsy, so the user gets an empty form.
    const phantom: Project = {
      id: '',
      user_id: 'cli',
      surface: 'web',
      brief: '',
      style_preset_id: '',
      duration_seconds: 60,
      phase: 'brief',
      use_narrator: true,
      total_cost_usd: 0,
      created_at: Date.now() / 1000,
      updated_at: Date.now() / 1000,
    };
    useStore.setState({ project: phantom, characters: [], shots: [] });
  };

  const deleteProject = useStore((s) => s.deleteProject);

  return (
    <div
      data-screen-label="00 Lobby"
      className="paper-bg"
      style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
    >
      <div
        style={{
          flex: '0 0 auto',
          padding: '14px 22px',
          borderBottom: '1.5px solid var(--rule)',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          background: 'var(--paper)',
        }}
      >
        <div className="mono" style={{ fontSize: 10, letterSpacing: '0.18em' }}>
          SPRITE · STUDIO
        </div>
        <span style={{ flex: 1 }} />
        <span className="pill">
          {projects.length} project{projects.length === 1 ? '' : 's'}
        </span>
        <button className="cta" onClick={startNew}>
          + new project
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '32px 48px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'space-between',
            marginBottom: 24,
          }}
        >
          <div>
            <div className="mono" style={{ fontSize: 9, marginBottom: 6 }}>
              00 · LOBBY
            </div>
            <h1 className="serif-it" style={{ fontSize: 56, margin: 0, lineHeight: 1 }}>
              Recent <span className="marker">cuts</span>.
            </h1>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {FILTERS.map((f) => (
              <span
                key={f}
                onClick={() => setFilter(f)}
                className={filter === f ? 'pill pill-accent' : 'pill'}
                style={{ cursor: 'pointer' }}
              >
                {f}
              </span>
            ))}
          </div>
        </div>

        {projects.length === 0 && filter !== 'all' && (
          <div className="sticky-note" style={{ display: 'inline-block', marginBottom: 18 }}>
            no projects in this filter. switch to "all" or start a new one.
          </div>
        )}

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 20,
            maxWidth: 1280,
          }}
        >
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onOpen={() => void openProject(p.id)}
              onDelete={() => deleteProject(p.id, filter)}
            />
          ))}
          <div
            className="box-soft pressy"
            onClick={startNew}
            style={{
              display: 'grid',
              placeItems: 'center',
              minHeight: 240,
              padding: 12,
              background: 'var(--paper)',
              cursor: 'pointer',
            }}
          >
            <div style={{ textAlign: 'center', color: 'var(--ink-faint)' }}>
              <div className="serif-it" style={{ fontSize: 36, lineHeight: 1 }}>
                +
              </div>
              <div className="hand" style={{ fontSize: 18, marginTop: 6 }}>
                new project
              </div>
              <div className="mono" style={{ fontSize: 9, marginTop: 6 }}>
                or paste a brief
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

interface CardProps {
  project: ProjectListEntry;
  onOpen: () => void;
  onDelete: () => Promise<void>;
}

function ProjectCard({ project, onOpen, onDelete }: CardProps): JSX.Element {
  const [hovered, setHovered] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const phaseColor =
    project.phase === 'done'
      ? 'var(--good)'
      : project.phase === 'render'
        ? 'var(--accent)'
        : 'var(--ink-faint)';
  const ago = formatAgo(project.updated_at);
  const thumbUrl = project.thumb_path ? thumbPathToUrl(project.thumb_path) : null;
  const title = project.title ?? (project.brief ? project.brief.slice(0, 40) : 'untitled');

  const handleCardClick = () => {
    // Suppress card-open while the confirm overlay is up. Clicking outside
    // the buttons should be a no-op, not navigate into a project we're
    // about to delete.
    if (confirming || deleting) return;
    onOpen();
  };

  const handleConfirmDelete = async (e: MouseEvent) => {
    e.stopPropagation();
    setDeleting(true);
    setError(null);
    try {
      await onDelete();
      // No setState(false) here; the parent unmounts this card on success.
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
    }
  };

  return (
    <div
      className="box-hand pressy"
      onClick={handleCardClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: 12,
        background: 'var(--paper)',
        cursor: confirming || deleting ? 'default' : 'pointer',
        position: 'relative',
        opacity: deleting ? 0.55 : 1,
        transition: 'opacity 120ms',
      }}
    >
      <div
        style={{
          position: 'relative',
          borderRadius: 3,
          overflow: 'hidden',
          border: '1px solid var(--rule)',
          height: 158,
        }}
      >
        {thumbUrl ? (
          <img
            src={thumbUrl}
            alt={title}
            style={{ width: '100%', height: 158, objectFit: 'cover', display: 'block' }}
          />
        ) : (
          <ShotStill
            kind={kindForShot({ characters_present: [], camera: null })}
            size={{ w: 280, h: 158 }}
          />
        )}
        {project.phase === 'render' && (
          <span
            style={{
              position: 'absolute',
              top: 8,
              right: 10,
              fontFamily: 'var(--mono)',
              fontSize: 9,
              color: 'var(--accent)',
              textTransform: 'uppercase',
              letterSpacing: '0.12em',
            }}
          >
            ○ live
          </span>
        )}
      </div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginTop: 10,
          alignItems: 'center',
        }}
      >
        <span className="mono" style={{ color: phaseColor, fontSize: 9 }}>
          ● {project.phase}
        </span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-faint)' }}>
          {ago}
        </span>
      </div>
      <div className="serif-it" style={{ fontSize: 24, marginTop: 4, lineHeight: 1.1 }}>
        {title}
      </div>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-faint)', marginTop: 4 }}>
        ${project.total_cost_usd.toFixed(2)}
      </div>

      {hovered && !confirming && !deleting && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(true);
            setError(null);
          }}
          aria-label="Delete project"
          title="Delete project"
          style={{
            position: 'absolute',
            top: 6,
            right: 6,
            width: 24,
            height: 24,
            borderRadius: 4,
            border: '1px solid var(--rule)',
            background: 'var(--paper)',
            cursor: 'pointer',
            display: 'grid',
            placeItems: 'center',
            fontSize: 12,
            lineHeight: 1,
            color: 'var(--ink-faint)',
            padding: 0,
          }}
        >
          ×
        </button>
      )}

      {confirming && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            inset: 0,
            background: 'rgba(255, 253, 248, 0.96)',
            borderRadius: 3,
            display: 'grid',
            placeItems: 'center',
            padding: 16,
            zIndex: 2,
          }}
        >
          <div style={{ textAlign: 'center', maxWidth: 240 }}>
            <div
              className="serif-it"
              style={{ fontSize: 22, lineHeight: 1.15, marginBottom: 6 }}
            >
              Delete this project?
            </div>
            <div
              className="mono"
              style={{
                fontSize: 9,
                color: 'var(--ink-faint)',
                marginBottom: 14,
                letterSpacing: '0.06em',
              }}
            >
              CANNOT BE UNDONE
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button
                type="button"
                className="cta"
                onClick={handleConfirmDelete}
                disabled={deleting}
                style={{
                  background: 'var(--bad, #b03a2e)',
                  color: 'var(--paper)',
                  borderColor: 'var(--bad, #b03a2e)',
                  opacity: deleting ? 0.6 : 1,
                  cursor: deleting ? 'wait' : 'pointer',
                }}
              >
                {deleting ? 'deleting…' : 'delete'}
              </button>
              <button
                type="button"
                className="pill"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirming(false);
                  setError(null);
                }}
                disabled={deleting}
                style={{ cursor: deleting ? 'wait' : 'pointer' }}
              >
                cancel
              </button>
            </div>
            {error && (
              <div
                className="mono"
                style={{
                  fontSize: 9,
                  color: 'var(--bad, #b03a2e)',
                  marginTop: 10,
                  wordBreak: 'break-word',
                }}
              >
                {error}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function formatAgo(ts: number): string {
  if (!ts || ts <= 0) return '...';
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

// thumb_path is an absolute filesystem path:
// /home/drew/.hermes/plugins/sprite-studio/projects/<pid>/shots/<sid>/reference.png
// The asset server serves <pid>/<rest>; strip the projects/ prefix.
function thumbPathToUrl(absolutePath: string): string {
  const m = absolutePath.match(/\/projects\/([^/]+)\/(.+)$/);
  if (!m) return '';
  return `${assetBase()}/${m[1]}/${m[2]}`;
}
