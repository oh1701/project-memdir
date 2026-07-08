#!/usr/bin/env node
// Function: Launch project-memdir hooks from Claude Code on every supported OS.
// Purpose: Avoid POSIX shell-only hook commands in Claude Code settings.

import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const action = process.argv[2];
const supportedActions = new Set(["session-start", "user-prompt-submit", "stop"]);

if (!supportedActions.has(action)) {
  console.error(`[memdir_claude_hook] failed: unsupported action ${JSON.stringify(action)}`);
  process.exit(1);
}

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginRoot = resolve(scriptDir, "../..");
const hookScript = join(pluginRoot, "hooks", "automation", "memdir_hook.py");
const payload = await readStdin();
const launchers =
  process.platform === "win32"
    ? [
        ["py", ["-3", hookScript, action]],
        ["python", [hookScript, action]],
        ["python3", [hookScript, action]],
      ]
    : [
        ["python3", [hookScript, action]],
        ["python", [hookScript, action]],
      ];

for (const [command, args] of launchers) {
  const result = spawnSync(command, args, {
    input: payload,
    encoding: "utf8",
    env: {
      ...process.env,
      PROJECT_MEMDIR_CLIENT: "claude",
      PYTHONUTF8: "1",
    },
    windowsHide: true,
  });

  if (result.error?.code === "ENOENT") {
    continue;
  }

  if (result.stdout) {
    process.stdout.write(result.stdout);
  }
  if (result.stderr) {
    process.stderr.write(result.stderr);
  }

  if (result.error) {
    console.error(`[memdir_claude_hook] failed: ${result.error.message}`);
    if (action === "stop") {
      process.exit(1);
    }
    continue;
  }

  const status = typeof result.status === "number" ? result.status : 1;
  if (status === 0) {
    process.exit(0);
  }
  if (action === "stop") {
    process.exit(status);
  }
}

console.error("[memdir_claude_hook] skipped: Python 3.11+ launcher failed; install Python or enable py/python on PATH.");
if (action !== "stop") {
  process.stdout.write('{"continue":true,"suppressOutput":true}\n');
  process.exit(0);
}
process.exit(1);

function readStdin() {
  return new Promise((resolvePayload) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => {
      resolvePayload(data);
    });
  });
}
