import { List, Icon, Color, ActionPanel, Action } from "@raycast/api";
import { useExec } from "@raycast/utils";

const PORT_LABELS: Record<string, string> = {
  "3000": "Gitea", "3030": "Grafana", "4222": "NATS",
  "5432": "PostgreSQL", "5433": "PG-Alt", "5434": "PG-Alt2",
  "6333": "Qdrant-HTTP", "6334": "Qdrant-gRPC",
  "6379": "Redis", "6380": "Redis-Alt", "6381": "Redis-Alt2",
  "8100": "API", "8108": "Typesense", "8222": "NATS-Mon",
  "9222": "Chrome-Debug", "11434": "Ollama",
  "14000": "Custom", "15432": "PG-Tunnel", "16379": "Redis-Tunnel",
  "49998": "Dolt",
};

interface Connection {
  process: string;
  port: string;
  addr: string;
  label: string;
  type: "tunnel" | "listen" | "external";
}

function parseLsof(output: string): Connection[] {
  const conns: Connection[] = [];
  const seen = new Set<string>();

  for (const line of output.split("\n").slice(1)) {
    const cols = line.trim().split(/\s+/);
    if (cols.length < 9) continue;

    const proc = cols[0];
    const addr = cols[8];
    const key = `${proc}:${addr}`;
    if (seen.has(key)) continue;
    seen.add(key);

    if (addr.includes("(LISTEN)")) {
      const port = addr.split(":").pop()?.replace("(LISTEN)", "") || "";
      conns.push({
        process: proc,
        port,
        addr: addr.split(":")[0],
        label: PORT_LABELS[port] || "",
        type: proc === "ssh" ? "tunnel" : "listen",
      });
    }
  }
  return conns.sort((a, b) => parseInt(a.port) - parseInt(b.port));
}

export default function NetworkCommand() {
  const { data, isLoading, revalidate } = useExec("lsof", ["-i", "-nP"], {
    keepPreviousData: true,
  });

  const conns = data ? parseLsof(data) : [];
  const tunnels = conns.filter((c) => c.type === "tunnel");
  const services = conns.filter((c) => c.type === "listen");

  return (
    <List isLoading={isLoading} searchBarPlaceholder="Filter services...">
      <List.Section title={`SSH Tunnels (${tunnels.length})`}>
        {tunnels.map((c, i) => (
          <List.Item
            key={`t-${i}`}
            icon={{ source: Icon.Lock, tintColor: Color.Orange }}
            title={`:${c.port}`}
            subtitle={c.label}
            accessories={[{ text: c.addr }]}
          />
        ))}
      </List.Section>
      <List.Section title={`Dienste (${services.length})`}>
        {services.map((c, i) => (
          <List.Item
            key={`s-${i}`}
            icon={{ source: Icon.Globe, tintColor: Color.Blue }}
            title={`${c.process} :${c.port}`}
            subtitle={c.label}
            accessories={[{ text: c.addr }]}
            actions={
              <ActionPanel>
                <Action title="Refresh" onAction={revalidate} shortcut={{ modifiers: ["cmd"], key: "r" }} />
                <Action.OpenInBrowser title="Open in Browser" url={`http://localhost:${c.port}`} />
              </ActionPanel>
            }
          />
        ))}
      </List.Section>
    </List>
  );
}
