/**
 * The protoLabs.studio bot mark, from protoContent's brand assets
 * (docs/assets/brand/protolabs-icon{,-outline}.svg). Reproduced verbatim
 * — per brand rules the mark is never deformed; only the icon background
 * may be recolored. ORBIS recolors it to its lavender chrome accent
 * (#9b87f2) so the splash mark matches the app, not the brand default
 * violet #7c3aed. Interim inline copy — will be replaced by the synced
 * asset from @protolabsai/design (protolabs-sync-assets) once 0.3.0 ships;
 * see brand-assets.config.json.
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
  variant?: 'flat' | 'outline' | 'white';
  className?: string;
}) {
  // flat: white robot on the lavender square. white: white robot, no bg
  // (for the dark title bar). outline: lavender strokes, no bg.
  const robotStroke = variant === 'outline' ? '#9b87f2' : '#ffffff';
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
        <rect x="16" y="16" width="224" height="224" rx="56" fill="#9b87f2" />
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
