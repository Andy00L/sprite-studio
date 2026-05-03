// Pure SVG-path builders for the connector overlay. P19a-3 hardcodes
// 'curved' (no tweaks panel per user decision #11), but the other two are
// preserved so a future preferences screen can swap them in.

export type ConnectorStyle = 'curved' | 'right-angle' | 'straight';

export function connectorPath(
  style: ConnectorStyle,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): string {
  if (style === 'straight') return `M ${x1} ${y1} L ${x2} ${y2}`;
  if (style === 'right-angle') {
    const midY = (y1 + y2) / 2;
    const dx = x2 > x1 ? 6 : -6;
    return `M ${x1} ${y1} L ${x1} ${midY - 6} Q ${x1} ${midY} ${x1 + dx} ${midY} L ${x2 - dx} ${midY} Q ${x2} ${midY} ${x2} ${midY + 6} L ${x2} ${y2}`;
  }
  const dy = y2 - y1;
  return `M ${x1} ${y1} C ${x1} ${y1 + dy * 0.5}, ${x2} ${y2 - dy * 0.5}, ${x2} ${y2}`;
}
