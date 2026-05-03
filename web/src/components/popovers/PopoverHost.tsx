import { useEffect } from 'react';
import type { JSX } from 'react';
import { useStore } from '../../state/store';
import { CharacterEditPopover } from './CharacterEditPopover';
import { CharacterAddPopover } from './CharacterAddPopover';
import { ShotEditPopover } from './ShotEditPopover';
import { TransitionPopover } from './TransitionPopover';

export function PopoverHost(): JSX.Element | null {
  const popover = useStore((s) => s.popover);
  const characters = useStore((s) => s.characters);
  const shots = useStore((s) => s.shots);
  const closePopover = useStore((s) => s.closePopover);
  const phase = useStore((s) => s.project?.phase);

  // Close any open popover if the phase changes underneath it (e.g. a chat
  // command advances the project from cast → timeline). Stale popovers
  // would still be wired to the old data.
  useEffect(() => {
    closePopover();
  }, [phase, closePopover]);

  // ESC closes the popover globally; document listener wins over
  // per-input focus state.
  useEffect(() => {
    if (popover.kind === 'none') return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') closePopover();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [popover.kind, closePopover]);

  if (popover.kind === 'none') return null;

  if (popover.kind === 'character-edit') {
    const c = characters.find((x) => x.id === popover.characterId);
    if (!c) {
      closePopover();
      return null;
    }
    return <CharacterEditPopover character={c} onClose={closePopover} />;
  }
  if (popover.kind === 'character-add') {
    return <CharacterAddPopover onClose={closePopover} />;
  }
  if (popover.kind === 'shot-edit') {
    const s = shots.find((x) => x.id === popover.shotId);
    if (!s) {
      closePopover();
      return null;
    }
    return (
      <ShotEditPopover mode={{ kind: 'edit', shot: s }} onClose={closePopover} />
    );
  }
  if (popover.kind === 'shot-add') {
    return (
      <ShotEditPopover
        mode={{ kind: 'add', insertAfterOrdinal: popover.insertAfterOrdinal }}
        onClose={closePopover}
      />
    );
  }
  if (popover.kind === 'transition') {
    const s = shots.find((x) => x.id === popover.shotId);
    if (!s) {
      closePopover();
      return null;
    }
    return (
      <TransitionPopover
        shotId={s.id}
        current={s.transition_to_next}
        onClose={closePopover}
      />
    );
  }
  return null;
}
