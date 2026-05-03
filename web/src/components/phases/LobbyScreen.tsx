import { useEffect, useState } from 'react';
import type { JSX } from 'react';
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
            <ProjectCard key={p.id} project={p} onOpen={() => void openProject(p.id)} />
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
}

function ProjectCard({ project, onOpen }: CardProps): JSX.Element {
  const phaseColor =
    project.phase === 'done'
      ? 'var(--good)'
      : project.phase === 'render'
        ? 'var(--accent)'
        : 'var(--ink-faint)';
  const ago = formatAgo(project.updated_at);
  const thumbUrl = project.thumb_path ? thumbPathToUrl(project.thumb_path) : null;
  const title = project.title ?? (project.brief ? project.brief.slice(0, 40) : 'untitled');

  return (
    <div
      className="box-hand pressy"
      onClick={onOpen}
      style={{ padding: 12, background: 'var(--paper)', cursor: 'pointer' }}
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
