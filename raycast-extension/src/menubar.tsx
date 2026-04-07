import { MenuBarExtra, Icon, open } from "@raycast/api";
import { useExec } from "@raycast/utils";

export default function MenubarCommand() {
  const { data } = useExec("ps", ["aux"], { keepPreviousData: true });

  let active = 0;
  let idle = 0;
  let totalCpu = 0;
  let totalMem = 0;

  if (data) {
    for (const line of data.split("\n")) {
      if (!line.includes("claude") || line.includes("grep")) continue;
      const cols = line.trim().split(/\s+/);
      if (cols.length < 11) continue;
      const cmd = cols[10];
      if (!cmd.includes("claude")) continue;
      if (cmd.includes("chrome-native") || cmd.includes("helper")) continue;

      const cpu = parseFloat(cols[2]);
      totalCpu += cpu;
      totalMem += parseInt(cols[5]) / 1024;

      if (cpu > 1.0) active++;
      else idle++;
    }
  }

  const total = active + idle;
  const title = total > 0
    ? `CC:${active}+${idle} ${totalCpu.toFixed(0)}%`
    : "CC:idle";

  return (
    <MenuBarExtra
      icon={total > 0 ? Icon.CircleFilled : Icon.Circle}
      title={title}
      tooltip={`${total} Claude instances | CPU ${totalCpu.toFixed(1)}% | RAM ${totalMem.toFixed(0)}MB`}
    >
      <MenuBarExtra.Section title="Claude Code">
        <MenuBarExtra.Item title={`${active} aktiv, ${idle} idle`} icon={Icon.Monitor} />
        <MenuBarExtra.Item title={`CPU: ${totalCpu.toFixed(1)}%`} icon={Icon.MemoryChip} />
        <MenuBarExtra.Item title={`RAM: ${totalMem.toFixed(0)}MB`} icon={Icon.MemoryStick} />
      </MenuBarExtra.Section>
      <MenuBarExtra.Section title="Quick Actions">
        <MenuBarExtra.Item
          title="Qdrant Dashboard"
          icon={Icon.Globe}
          onAction={() => open("http://localhost:6333/dashboard")}
        />
        <MenuBarExtra.Item
          title="TUI Dashboard"
          icon={Icon.Terminal}
          onAction={() => {
            const { execSync } = require("child_process");
            execSync(`osascript -e 'tell application "Ghostty" to activate'`);
          }}
        />
      </MenuBarExtra.Section>
    </MenuBarExtra>
  );
}
