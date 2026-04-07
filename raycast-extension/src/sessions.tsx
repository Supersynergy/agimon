import { List, Icon, Color, ActionPanel, Action, Detail } from "@raycast/api";
import { useExec } from "@raycast/utils";
import { useState } from "react";
import { homedir } from "os";
import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";

interface Session {
  id: string;
  firstMessage: string;
  messageCount: number;
  subagentCount: number;
  lastModified: Date;
  isActive: boolean;
}

function loadSessions(): Session[] {
  const projectDir = join(homedir(), ".claude", "projects", "-Users-master");
  const sessions: Session[] = [];
  const cutoff = Date.now() - 300_000; // 5 minutes

  try {
    const files = readdirSync(projectDir)
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => ({
        name: f,
        path: join(projectDir, f),
        mtime: statSync(join(projectDir, f)).mtimeMs,
      }))
      .sort((a, b) => b.mtime - a.mtime)
      .slice(0, 20);

    for (const file of files) {
      const content = readFileSync(file.path, "utf-8");
      const lines = content.split("\n").filter(Boolean);
      let firstMsg = "";
      let msgCount = 0;
      let subagentCount = 0;

      for (const line of lines) {
        try {
          const rec = JSON.parse(line);
          if (rec.type === "user" || rec.type === "assistant") msgCount++;
          if (rec.type === "user" && !firstMsg) {
            const msg = rec.message?.content;
            if (typeof msg === "string") firstMsg = msg.slice(0, 80);
            else if (Array.isArray(msg)) {
              const textBlock = msg.find((b: { type: string }) => b.type === "text");
              if (textBlock) firstMsg = textBlock.text?.slice(0, 80) || "";
            }
          }
        } catch {
          /* skip invalid JSON */
        }
      }

      // Check subagents
      const subDir = join(projectDir, file.name.replace(".jsonl", ""), "subagents");
      try {
        subagentCount = readdirSync(subDir).filter((f) => f.endsWith(".jsonl")).length;
      } catch {
        /* no subagents */
      }

      sessions.push({
        id: file.name.replace(".jsonl", ""),
        firstMessage: firstMsg || "(kein Text)",
        messageCount: msgCount,
        subagentCount,
        lastModified: new Date(file.mtime),
        isActive: file.mtime > cutoff,
      });
    }
  } catch {
    /* directory not found */
  }

  return sessions;
}

export default function SessionsCommand() {
  const sessions = loadSessions();
  const active = sessions.filter((s) => s.isActive);
  const recent = sessions.filter((s) => !s.isActive);

  return (
    <List searchBarPlaceholder="Filter sessions...">
      <List.Section title={`Aktiv (${active.length})`}>
        {active.map((s) => (
          <List.Item
            key={s.id}
            icon={{ source: Icon.CircleFilled, tintColor: Color.Green }}
            title={s.firstMessage}
            subtitle={s.id.slice(0, 8)}
            accessories={[
              { text: `${s.messageCount} msgs`, icon: Icon.Message },
              { text: `${s.subagentCount} agents`, icon: Icon.TwoPeople },
              {
                date: s.lastModified,
                tooltip: s.lastModified.toLocaleString(),
              },
            ]}
          />
        ))}
      </List.Section>
      <List.Section title={`Letzte Sessions (${recent.length})`}>
        {recent.map((s) => (
          <List.Item
            key={s.id}
            icon={{ source: Icon.Circle, tintColor: Color.SecondaryText }}
            title={s.firstMessage}
            subtitle={s.id.slice(0, 8)}
            accessories={[
              { text: `${s.messageCount} msgs` },
              { text: `${s.subagentCount} agents` },
              { date: s.lastModified },
            ]}
          />
        ))}
      </List.Section>
    </List>
  );
}
