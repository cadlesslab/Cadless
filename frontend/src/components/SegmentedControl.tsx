/** Compact segmented toggle for viewport mode pickers etc. */
export interface Segment<T extends string> {
  value: T;
  label: string;
}

export function SegmentedControl<T extends string>({
  value,
  segments,
  onChange,
  ariaLabel,
}: {
  value: T;
  segments: Segment<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel}>
      {segments.map((s) => (
        <button
          key={s.value}
          role="radio"
          aria-checked={s.value === value}
          className={`segment ${s.value === value ? "segment-active" : ""}`}
          onClick={() => onChange(s.value)}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
