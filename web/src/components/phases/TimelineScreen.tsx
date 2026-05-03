import { useEffect, useRef, useState } from 'react';
import type { JSX, CSSProperties } from 'react';
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

// dnd-kit sortable docs: https://docs.dndkit.com/presets/sortable
// React 19 ref-as-prop: https://react.dev/blog/2024/12/05/react-19

import { useStore } from '../../state/store';
import { PhaseCanvas } from './PhaseCanvas';
import { CharacterAnchor } from '../timeline/CharacterAnchor';
import { ShotCard } from '../timeline/ShotCard';
import { TransitionPill } from '../timeline/TransitionPill';
import { ConnectorOverlay } from '../timeline/ConnectorOverlay';
import { TimeAxis } from '../timeline/TimeAxis';
import {
  ADD_SHOT_W,
  CARD_H,
  PAD_X,
  SHOT_GAP,
  placeShots,
  type PlacedShot,
} from '../../lib/shotMath';
import type { Character } from '../../types/sprite';

// Poll cadence + safety timeout for background timeline generation kicked
// off by /sprite_approve_cast. 3s matches the existing render-status poll
// interval (state/store.ts pollIntervalMs default). 5 min is the watchdog
// that surfaces a "taking longer than usual" hint and a retry CTA.
const TIMELINE_POLL_MS = 3000;
const TIMELINE_GEN_WATCHDOG_MS = 5 * 60 * 1000;

export function TimelineScreen(): JSX.Element {
  const project = useStore((s) => s.project);
  const characters = useStore((s) => s.characters);
  const shots = useStore((s) => s.shots);
  const reorderShots = useStore((s) => s.reorderShots);
  const reorderCast = useStore((s) => s.reorderCast);
  const openPopover = useStore((s) => s.openPopover);
  const refreshShow = useStore((s) => s.refreshShow);
  const generateTimeline = useStore((s) => s.generateTimeline);

  const projectId = project?.id ?? '';
  const timelineStatus = project?.timeline_status ?? (shots.length > 0 ? 'ready' : 'generating');
  const isGenerating = timelineStatus === 'generating';
  const [watchdogTripped, setWatchdogTripped] = useState(false);
  const { placed, stripWidth, totalSeconds } = placeShots(shots);

  const canvasRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const charRefs = useRef<Record<string, HTMLElement | null>>({});
  const shotRefs = useRef<Record<string, HTMLElement | null>>({});

  // Refresh on mount in case the user landed here from a slash command in
  // chat (App.tsx already hydrates on app boot, but a phase flip mid-session
  // wouldn't otherwise pull the new shot list).
  useEffect(() => {
    if (projectId) void refreshShow(projectId);
  }, [projectId, refreshShow]);

  // Poll /sprite_show while the background timeline-gen task is still in
  // flight (timeline_status='generating', empty shots). Flips off as soon
  // as shots arrive or the backend reports failure. The cleanup function
  // cancels the chained setTimeout so navigating away doesn't leak timers.
  useEffect(() => {
    if (!projectId || !isGenerating) return;
    let cancelled = false;
    const tick = (): void => {
      if (cancelled) return;
      void refreshShow(projectId).finally(() => {
        if (cancelled) return;
        timer = window.setTimeout(tick, TIMELINE_POLL_MS);
      });
    };
    let timer = window.setTimeout(tick, TIMELINE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [projectId, isGenerating, refreshShow]);

  // Watchdog: if timeline_status stays 'generating' for 5 minutes, surface
  // a hint that generation is taking longer than usual + a retry CTA. The
  // reset on cycle change runs in a queued microtask so React doesn't see a
  // synchronous setState inside the effect body (react-hooks rule).
  useEffect(() => {
    queueMicrotask(() => setWatchdogTripped(false));
    if (!isGenerating) return;
    const id = window.setTimeout(
      () => setWatchdogTripped(true),
      TIMELINE_GEN_WATCHDOG_MS,
    );
    return () => window.clearTimeout(id);
  }, [isGenerating, projectId]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onShotDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIdx = shots.findIndex((s) => s.id === active.id);
    const newIdx = shots.findIndex((s) => s.id === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    const reordered = arrayMove(shots, oldIdx, newIdx);
    void reorderShots(reordered.map((s) => s.id));
  };

  const onCharDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIdx = characters.findIndex((c) => c.id === active.id);
    const newIdx = characters.findIndex((c) => c.id === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    const reordered = arrayMove(characters, oldIdx, newIdx);
    void reorderCast(reordered.map((c) => c.id));
  };

  const onAddShot = (): void => {
    openPopover({ kind: 'shot-add', insertAfterOrdinal: shots.length });
  };

  return (
    <PhaseCanvas phase="timeline" canvasRef={canvasRef}>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onCharDragEnd}>
        <SortableContext
          items={characters.map((c) => c.id)}
          strategy={horizontalListSortingStrategy}
        >
          <div
            style={{
              position: 'absolute',
              top: 24,
              left: '4%',
              right: '4%',
              display: 'flex',
              gap: 16,
              alignItems: 'flex-start',
              flexWrap: 'wrap',
            }}
          >
            {characters.map((c) => (
              <SortableCharacter
                key={c.id}
                character={c}
                projectId={projectId}
                onClick={() =>
                  openPopover({ kind: 'character-edit', characterId: c.id })
                }
                setRef={(el) => {
                  charRefs.current[c.id] = el;
                }}
              />
            ))}
            <div
              className="box-soft pressy"
              onClick={() => openPopover({ kind: 'character-add' })}
              style={{
                padding: 16,
                color: 'var(--accent)',
                textAlign: 'center',
                alignSelf: 'stretch',
                display: 'grid',
                placeItems: 'center',
                minWidth: 130,
                cursor: 'pointer',
              }}
            >
              <span className="serif-it" style={{ fontSize: 18 }}>
                {'+ add'}
                <br />
                {'character'}
              </span>
            </div>
          </div>
        </SortableContext>
      </DndContext>

      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          top: '54%',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ flex: '0 0 auto', padding: '0 8px 4px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '0 16px' }}>
            <span
              className="mono"
              style={{ fontSize: 9, color: 'var(--ink-faint)', letterSpacing: 1 }}
            >
              TIMELINE
            </span>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-faint)' }}>
              ·
            </span>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-faint)' }}>
              {`${totalSeconds}s · ${shots.length} shots`}
            </span>
            <span style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: 8, color: 'var(--ink-faint)' }}>
              ↔ scroll
            </span>
          </div>
        </div>

        <div
          ref={scrollRef}
          style={{
            position: 'relative',
            flex: '1 1 auto',
            minHeight: 0,
            overflowX: 'auto',
            overflowY: 'visible',
            borderTop: '1px dashed var(--rule-soft)',
            borderBottom: '1px dashed var(--rule-soft)',
          }}
        >
          <div style={{ position: 'relative', width: stripWidth }}>
            <TimeAxis stripWidth={stripWidth} totalSeconds={totalSeconds} placed={placed} />

            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onShotDragEnd}>
              <SortableContext
                items={placed.map((p) => p.id)}
                strategy={horizontalListSortingStrategy}
              >
                <div style={{ position: 'relative', height: CARD_H, marginTop: 4 }}>
                  {placed.map((s, i) => (
                    <SortableShot
                      key={s.id}
                      placed={s}
                      ordinal={i + 1}
                      characters={characters}
                      projectId={projectId}
                      onClick={() =>
                        openPopover({ kind: 'shot-edit', shotId: s.id })
                      }
                      setRef={(el) => {
                        shotRefs.current[s.id] = el;
                      }}
                    />
                  ))}
                  {placed.slice(0, -1).map((s, i) => {
                    const next = placed[i + 1];
                    const tx = (s.x + s.w + next.x) / 2;
                    return (
                      <div
                        key={`t-${s.id}`}
                        style={{
                          position: 'absolute',
                          left: tx - 26,
                          top: 50,
                          width: 52,
                          zIndex: 2,
                        }}
                      >
                        <TransitionPill
                          current={s.transition_to_next}
                          onClick={() =>
                            openPopover({ kind: 'transition', shotId: s.id })
                          }
                        />
                      </div>
                    );
                  })}
                  {placed.length > 0 && (() => {
                    const last = placed[placed.length - 1];
                    const ax = last.x + last.w + SHOT_GAP;
                    return (
                      <div
                        className="dashed-accent pressy"
                        onClick={onAddShot}
                        style={{
                          position: 'absolute',
                          left: ax,
                          top: 4,
                          width: ADD_SHOT_W,
                          height: CARD_H - 16,
                          display: 'grid',
                          placeItems: 'center',
                          textAlign: 'center',
                          color: 'var(--accent)',
                          padding: 8,
                          background: 'var(--paper)',
                          cursor: 'pointer',
                        }}
                      >
                        <div>
                          <div className="serif-it" style={{ fontSize: 22, lineHeight: 1 }}>
                            +
                          </div>
                          <div className="hand" style={{ fontSize: 13 }}>
                            add shot
                          </div>
                          <div className="mono" style={{ fontSize: 7, marginTop: 4 }}>
                            {'or chat'}
                            <br />
                            {'/sprite_add_shot'}
                          </div>
                        </div>
                      </div>
                    );
                  })()}
                  {shots.length === 0 && (
                    <div
                      className="sticky-note"
                      style={{
                        position: 'absolute',
                        left: PAD_X,
                        top: 30,
                        maxWidth: 320,
                      }}
                    >
                      {isGenerating && !watchdogTripped && (
                        <>
                          <div className="serif-it" style={{ fontSize: 18 }}>
                            generating timeline...
                          </div>
                          <div
                            className="mono"
                            style={{
                              fontSize: 9,
                              marginTop: 6,
                              color: 'var(--ink-soft)',
                            }}
                          >
                            usually 30-90s. shots will appear here when ready.
                          </div>
                        </>
                      )}
                      {isGenerating && watchdogTripped && (
                        <>
                          <div className="serif-it" style={{ fontSize: 16 }}>
                            still generating...
                          </div>
                          <div
                            className="mono"
                            style={{
                              fontSize: 9,
                              marginTop: 6,
                              color: 'var(--ink-soft)',
                            }}
                          >
                            taking longer than usual. retry below if needed.
                          </div>
                          <button
                            className="cta"
                            onClick={() => void generateTimeline()}
                            style={{ marginTop: 8, fontSize: 11 }}
                          >
                            retry timeline
                          </button>
                        </>
                      )}
                      {!isGenerating && (
                        <div className="hand" style={{ fontSize: 13 }}>
                          no shots yet. add one with the +, or run
                          /sprite_timeline in chat.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </SortableContext>
            </DndContext>
          </div>
        </div>
      </div>

      <ConnectorOverlay
        canvasRef={canvasRef}
        scrollRef={scrollRef}
        charRefs={charRefs}
        shotRefs={shotRefs}
        characters={characters}
        shots={shots}
      />
    </PhaseCanvas>
  );
}

interface SortableCharProps {
  character: Character;
  projectId: string;
  onClick: () => void;
  setRef: (el: HTMLElement | null) => void;
}

function SortableCharacter({ character, projectId, onClick, setRef }: SortableCharProps): JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: character.id,
  });
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.55 : 1,
    cursor: 'grab',
  };
  const mergedRef = (el: HTMLDivElement | null) => {
    setNodeRef(el);
    setRef(el);
  };
  return (
    <div ref={mergedRef} style={style} {...attributes} {...listeners}>
      <CharacterAnchor character={character} projectId={projectId} onClick={onClick} />
    </div>
  );
}

interface SortableShotProps {
  placed: PlacedShot;
  ordinal: number;
  characters: Character[];
  projectId: string;
  onClick: () => void;
  setRef: (el: HTMLElement | null) => void;
}

function SortableShot({
  placed,
  ordinal,
  characters,
  projectId,
  onClick,
  setRef,
}: SortableShotProps): JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: placed.id,
  });
  const style: CSSProperties = {
    position: 'absolute',
    left: placed.x,
    top: 4,
    width: placed.w,
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.55 : 1,
    zIndex: isDragging ? 10 : 1,
  };
  const mergedRef = (el: HTMLDivElement | null) => {
    setNodeRef(el);
    setRef(el);
  };
  return (
    <div ref={mergedRef} style={style} {...attributes} {...listeners}>
      <ShotCard
        shot={placed}
        ordinal={ordinal}
        width={placed.w}
        height={CARD_H}
        characters={characters}
        projectId={projectId}
        onClick={onClick}
      />
    </div>
  );
}
