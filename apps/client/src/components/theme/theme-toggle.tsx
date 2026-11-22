import { IconDeviceDesktop, IconMoon, IconSun } from "@tabler/icons-react";
import { useTheme } from "@/components/theme/theme-provider.tsx";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group.tsx";
import { isThemeIntent } from "@/lib/theme.ts";
import { m } from "@/paraglide/messages.js";

export function ThemeToggle() {
  const { intent, setIntent } = useTheme();

  return (
    <ToggleGroup
      aria-label={m.nav_theme()}
      onValueChange={([next]) => {
        if (isThemeIntent(next)) {
          setIntent(next);
        }
      }}
      size="sm"
      spacing={0}
      value={[intent]}
      variant="outline"
    >
      <ToggleGroupItem aria-label={m.nav_system_theme()} value="system">
        <IconDeviceDesktop />
      </ToggleGroupItem>
      <ToggleGroupItem aria-label={m.nav_light_theme()} value="light">
        <IconSun />
      </ToggleGroupItem>
      <ToggleGroupItem aria-label={m.nav_dark_theme()} value="dark">
        <IconMoon />
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
