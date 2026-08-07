/** Numeric slider (used by the parameter inspector) on Radix Slider. */
import * as SliderPrimitive from "@radix-ui/react-slider";

export function Slider({
  value,
  min,
  max,
  step = 0.1,
  onValueChange,
  onValueCommit,
  label,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onValueChange?: (v: number) => void;
  onValueCommit?: (v: number) => void;
  label: string;
}) {
  return (
    <SliderPrimitive.Root
      className="slider"
      value={[value]}
      min={min}
      max={max}
      step={step}
      onValueChange={(v) => onValueChange?.(v[0])}
      onValueCommit={(v) => onValueCommit?.(v[0])}
      aria-label={label}
    >
      <SliderPrimitive.Track className="slider-track">
        <SliderPrimitive.Range className="slider-range" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="slider-thumb" aria-label={label} />
    </SliderPrimitive.Root>
  );
}
