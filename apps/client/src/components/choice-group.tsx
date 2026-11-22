import { useId } from "react";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group.tsx";

export interface Choice<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

/** One choice out of a few, each explained in a sentence; rows are full-width targets. */
export function ChoiceGroup<T extends string>({
  label,
  help,
  value,
  onChange,
  options,
}: {
  label: string;
  help?: string;
  value: T;
  onChange: (value: T) => void;
  options: readonly Choice<T>[];
}) {
  const id = useId();
  return (
    <fieldset>
      <legend className="text-sm font-medium">{label}</legend>
      {help && <p className="text-muted-foreground mt-1 text-sm">{help}</p>}
      <RadioGroup
        aria-label={label}
        className="mt-2 gap-0 divide-y border-y"
        onValueChange={(next) => {
          const match = options.find((option) => option.value === next);
          if (match) {
            onChange(match.value);
          }
        }}
        value={value}
      >
        {options.map((option) => (
          <label
            className="flex min-h-11 cursor-pointer items-start gap-3 py-3"
            htmlFor={`${id}-${option.value}`}
            key={option.value}
          >
            <RadioGroupItem
              className="mt-0.5"
              id={`${id}-${option.value}`}
              value={option.value}
            />
            <span className="min-w-0">
              <span className="block font-medium">{option.label}</span>
              {option.hint && (
                <span className="text-muted-foreground block">
                  {option.hint}
                </span>
              )}
            </span>
          </label>
        ))}
      </RadioGroup>
    </fieldset>
  );
}
