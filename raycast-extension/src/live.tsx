import { List, Icon, Color, ActionPanel, Action } from "@raycast/api";
import { useExec } from "@raycast/utils";

interface ClaudeProcess {
  pid: string;
  cpu: string;
  mem: string;
  tty: string;
  started: string;
  status: "active" | "idle";
}

function parseProcesses(output: string): ClaudeProcess[] {
  return output
    .split("\n")
    .filter((line) => line.includes("claude") && !line.includes("grep"))
    .map((line) => {
      const cols = line.trim().split(/\s+/);
      if (cols.length < 11) return null;
      const cpu = parseFloat(cols[2]);
      return {
        pid: cols[1],
        cpu: cols[2],
        mem: (parseInt(cols[5]) / 1024).toFixed(0),
        tty: cols[6],
        started: cols[8],
        status: cpu > 1.0 ? ("active" as const) : ("idle" as const),
      };
    })
    .filter((p): p is ClaudeProcess => p !== null);
}

export default function LiveCommand() {
  const { data, isLoading, revalidate } = useExec("ps", ["aux"], {
    keepPreviousData: true,
  });

  const processes = data ? parseProcesses(data) : [];
  const active = processes.filter((p) => p.status === "active").length;
  const idle = processes.filter((p) => p.status === "idle").length;

  return (
    <List
      isLoading={isLoading}
      searchBarPlaceholder="Filter Claude instances..."
      navigationTitle={`${active} aktiv, ${idle} idle`}
    >
      <List.Section title={`Claude Code Instanzen (${processes.length})`}>
        {processes.map((proc) => (
          <List.Item
            key={proc.pid}
            icon={{
              source: Icon.Circle,
              tintColor: proc.status === "active" ? Color.Orange : Color.SecondaryText,
            }}
            title={`PID ${proc.pid}`}
            subtitle={proc.tty}
            accessories={[
              { text: `CPU ${proc.cpu}%` },
              { text: `${proc.mem}MB` },
              { text: proc.started, icon: Icon.Clock },
            ]}
            actions={
              <ActionPanel>
                <Action title="Refresh" onAction={revalidate} shortcut={{ modifiers: ["cmd"], key: "r" }} />
                <Action.CopyToClipboard title="Copy PID" content={proc.pid} />
              </ActionPanel>
            }
          />
        ))}
      </List.Section>
    </List>
  );
}
