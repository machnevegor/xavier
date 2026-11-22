import { execFile } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const username = process.env.XAVIER_USERNAME ?? "e2e";
const password = process.env.XAVIER_PASSWORD ?? "e2e-password";
const base = process.env.XAVIER_URL ?? "http://localhost:8080";

async function signInStatus(): Promise<number> {
  const response = await fetch(`${base}/api/v1/auth/session`, {
    body: JSON.stringify({ data: { password, username } }),
    headers: { "content-type": "application/json" },
    method: "POST",
  });
  return response.status;
}

function xavier(args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = execFile(
      "docker",
      ["compose", "exec", "-T", "server", ...args],
      { cwd: root },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error((stderr || stdout || error.message).trim()));
          return;
        }
        resolve(stdout);
      },
    );
    child.stdin?.end(`${password}\n`);
  });
}

/** Make sure the stack has the user the browser tests sign in as. */
export default async function globalSetup(): Promise<void> {
  let status: number;
  try {
    status = await signInStatus();
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new Error(
      `Xavier is not reachable at ${base} (${reason}). Start the stack with docker compose before pnpm test:e2e.`,
    );
  }
  if (status === 201) {
    return;
  }
  if (status !== 401) {
    throw new Error(`Sign-in at ${base} answered ${status}.`);
  }
  try {
    await xavier([
      "xavier",
      "users",
      "create",
      username,
      "--name",
      "E2E",
      "--password-stdin",
    ]);
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    if (!message.includes("already exists")) {
      throw new Error(`Could not create the e2e user: ${message}`);
    }
    await xavier([
      "xavier",
      "users",
      "set-password",
      username,
      "--password-stdin",
    ]);
  }
  const again = await signInStatus();
  if (again !== 201) {
    throw new Error(`The seeded user still cannot sign in (${again}).`);
  }
}
