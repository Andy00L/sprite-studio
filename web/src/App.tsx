import { useEffect } from 'react';
import type { JSX } from 'react';
import { useStore, selectEffectivePhase } from './state/store';
import { Header } from './components/chrome/Header';
import { ChatDock } from './components/chrome/ChatDock';
import { PopoverHost } from './components/popovers/PopoverHost';
import { LobbyScreen } from './components/phases/LobbyScreen';
import { BriefScreen } from './components/phases/BriefScreen';
import { CastScreen } from './components/phases/CastScreen';
import { TimelineScreen } from './components/phases/TimelineScreen';
import { RenderScreen } from './components/phases/RenderScreen';
import { DoneScreen } from './components/phases/DoneScreen';
import type { ProjectPhase } from './types/sprite';

// Maps each phase to the action behind the Header's advance button. Returns
// undefined for phases whose advance is owned by the screen itself (brief
// has its own "cast it" button) or doesn't make sense (render is in-flight).
// Header disables the button when onAdvance is undefined.
function advanceFor(phase: ProjectPhase | null | undefined): (() => void) | undefined {
  if (!phase) return undefined;
  switch (phase) {
    case 'brief':
      return undefined;
    case 'cast':
      return () => void useStore.getState().approveCast();
    case 'timeline':
      return () => {
        void (async () => {
          await useStore.getState().approveTimeline();
          await useStore.getState().startRender();
        })();
      };
    case 'render':
      return undefined;
    case 'done':
    case 'failed':
      return () => void useStore.getState().sendRaw('/sprite_render');
  }
}

export function App(): JSX.Element {
  const project = useStore((s) => s.project);
  const error = useStore((s) => s.error);
  const setError = useStore((s) => s.setError);
  const setActiveProject = useStore((s) => s.setActiveProject);
  const checkAssets = useStore((s) => s.checkAssets);
  const refreshShow = useStore((s) => s.refreshShow);
  // P19a-22: route by effectivePhase so a click in the Header phase strip
  // flips the screen even when the project hasn't moved (read-only nav on
  // a done/failed project). When viewedPhase is null this matches project.phase.
  const effPhase = useStore(selectEffectivePhase);

  useEffect(() => {
    void checkAssets();
    void refreshShow();
  }, [checkAssets, refreshShow]);

  let body: JSX.Element;
  if (!project) {
    body = <LobbyScreen />;
  } else if (effPhase === 'brief') {
    // Key by project id so transitions between drafts (or phantom -> real)
    // remount the screen and rebuild initial form state from scratch.
    body = <BriefScreen key={project.id || 'new'} />;
  } else if (effPhase === 'cast') {
    body = <CastScreen />;
  } else if (effPhase === 'timeline') {
    body = <TimelineScreen />;
  } else if (effPhase === 'render') {
    body = <RenderScreen />;
  } else if (effPhase === 'done' || effPhase === 'failed') {
    body = <DoneScreen />;
  } else {
    body = <LobbyScreen />;
  }

  const showHeader = Boolean(project);

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--paper)',
      }}
    >
      {showHeader && (
        <Header
          onBack={() => {
            setActiveProject(null);
            useStore.setState({ project: null, characters: [], shots: [] });
          }}
          onAdvance={advanceFor(project?.phase)}
        />
      )}
      {error && (
        <div
          style={{
            background: 'var(--accent-tint-strong)',
            color: 'var(--accent)',
            padding: '6px 16px',
            fontFamily: 'var(--mono)',
            fontSize: 11,
            borderBottom: '1px solid var(--accent)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span>error · {error}</span>
          <span style={{ flex: 1 }} />
          <button
            onClick={() => setError(null)}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--accent)',
              fontSize: 14,
            }}
          >
            ✕
          </button>
        </div>
      )}
      <main
        style={{
          flex: 1,
          position: 'relative',
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {body}
      </main>
      <PopoverHost />
      {showHeader && <ChatDock />}
    </div>
  );
}
