/**
 * The protoLabs.studio bot mark, from protoContent's brand assets
 * (docs/assets/brand/protolabs-icon{,-outline}.svg). Reproduced verbatim
 * — per brand rules the mark is never deformed; only the icon background
 * may be recolored, and the default is the brand violet #7c3aed.
 *
 * - `flat` (default): violet rounded square + white robot — the app/brand
 *   icon at moderate sizes (splash, about).
 * - `outline`: face-only violet strokes on transparent — for inline-with-
 *   text / no-compete contexts.
 */
export function ProtoLabsIcon({
  size = 64,
  variant = 'flat',
  className,
}: {
  size?: number;
  variant?: 'flat' | 'outline';
  className?: string;
}) {
  const robotStroke = variant === 'flat' ? '#ffffff' : '#7c3aed';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 256 256"
      role="img"
      aria-label="protoLabs.studio"
      className={className}
    >
      {variant === 'flat' && (
        <rect x="16" y="16" width="224" height="224" rx="56" fill="#7c3aed" />
      )}
      <g
        transform="translate(224, 32) scale(-8, 8)"
        fill="none"
        stroke={robotStroke}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 8V4H8" />
        <rect width="16" height="12" x="4" y="8" rx="2" />
        <path d="M2 14h2" />
        <path d="M20 14h2" />
        <path d="M15 13v2" />
        <path d="M9 13v2" />
      </g>
    </svg>
  );
}
