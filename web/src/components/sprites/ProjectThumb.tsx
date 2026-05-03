import type { JSX } from 'react';
import type { ShotKind } from '../../lib/design';
import { ShotStill } from './ShotStill';

interface Props {
  kind?: ShotKind;
  w?: number;
  h?: number;
}

export function ProjectThumb({ kind = 'wide', w = 180, h = 104 }: Props): JSX.Element {
  return <ShotStill kind={kind} size={{ w, h }} />;
}
