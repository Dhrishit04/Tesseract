interface TesseractMarkProps {
  size?: number;
  className?: string;
}

/** The hypercube mark: an inner and outer square joined at the corners. */
export function TesseractMark({ size = 28, className }: Readonly<TesseractMarkProps>) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="4" y="4" width="40" height="40" strokeWidth="2" />
      <rect x="16" y="16" width="16" height="16" strokeWidth="2" />
      <path d="M4 4 16 16M44 4 32 16M44 44 32 32M4 44 16 32" strokeWidth="1.25" />
    </svg>
  );
}
