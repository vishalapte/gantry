/**
 * gantry-guard: gantry's shell-command rules and env for Pi.
 *
 * Installed to ~/.gantry/pi/gantry-guard.ts and linked into ~/.pi/agent/extensions/.
 * Each bash call goes to ~/.gantry/hooks/guard.py --pi, which reads ~/.gantry/permissions.json:
 * deny blocks, allow runs, anything else asks. With no screen to ask on (pi -p), it blocks
 * what it would ask. If the guard cannot run or answers nonsense, the call is blocked.
 *
 * ~/.gantry/env.json (GIT_CONFIG_* for gantry's git hooks, ENINE_MAIL_*) is set on this
 * process's environment, which Pi copies into every bash call it spawns.
 */

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const GANTRY = join(homedir(), ".gantry");
const GUARD = join(GANTRY, "hooks", "guard.py");

type Verdict = { decision: "allow" | "ask" | "deny"; reason: string };

function verdict(command: string): Verdict {
	const run = spawnSync(GUARD, ["--pi"], { input: JSON.stringify({ command }), encoding: "utf-8" });
	if (run.error || run.status !== 0) {
		const why = run.error ? run.error.message : (run.stderr || `exit ${run.status}`).trim();
		return { decision: "deny", reason: `gantry guard failed: ${why}` };
	}
	try {
		const parsed = JSON.parse(run.stdout) as Verdict;
		if (["allow", "ask", "deny"].includes(parsed.decision)) return parsed;
	} catch {}
	return { decision: "deny", reason: `gantry guard answered: ${run.stdout.trim()}` };
}

export default function (pi: any) {
	try {
		const env = JSON.parse(readFileSync(join(GANTRY, "env.json"), "utf-8")) as Record<string, string>;
		for (const [key, value] of Object.entries(env)) process.env[key] = value;
	} catch (err) {
		console.error(`gantry-guard: env not set: ${(err as Error).message}`);
	}

	pi.on("tool_call", async (event: any, ctx: any) => {
		if (event.toolName !== "bash") return undefined;
		const command = String(event.input.command ?? "");
		const { decision, reason } = verdict(command);
		if (decision === "allow") return undefined;
		if (decision === "deny") return { block: true, reason: `gantry: ${reason}` };
		if (!ctx.hasUI) return { block: true, reason: `gantry: would ask (${reason}); no screen to ask on` };
		const ok = await ctx.ui.confirm("gantry: run this command?", `${command}\n\n${reason}`);
		return ok ? undefined : { block: true, reason: "gantry: refused by the owner" };
	});
}
