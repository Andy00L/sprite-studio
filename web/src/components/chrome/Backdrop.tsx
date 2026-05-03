import type { JSX, ReactNode, MouseEvent } from 'react';

interface Props {
  onClose: () => void;
  children?: ReactNode;
}

export function Backdrop({ onClose, children }: Props): JSX.Element {
  const handleClick = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };
  return (
    <div className="backdrop" onClick={handleClick}>
      {children}
    </div>
  );
}
