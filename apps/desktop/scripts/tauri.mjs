import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import { run } from "@tauri-apps/cli";

const cargo = process.platform === "win32" ? "cargo.exe" : "cargo";
const root = fileURLToPath(new URL("../../../", import.meta.url));

function cargoAvailable(env) {
  return spawnSync(cargo, ["--version"], { env, stdio: "ignore" }).status === 0;
}

if (!cargoAvailable(process.env)) {
  const cargoHome = join(root, ".cargo");
  const cargoBin = join(cargoHome, "bin");
  const rustupHome = join(root, ".rustup");
  // Windows may spell the inherited PATH key as Path.
  const pathKey = Object.keys(process.env).find((key) => key.toUpperCase() === "PATH") ?? "PATH";
  const path = [cargoBin, process.env[pathKey]].filter(Boolean).join(delimiter);
  const localEnv = { ...process.env, [pathKey]: path, CARGO_HOME: cargoHome, RUSTUP_HOME: rustupHome };

  if (!existsSync(join(cargoBin, cargo)) || !cargoAvailable(localEnv)) {
    console.error("Cargo is unavailable. Install a Rust toolchain with Cargo, make sure `cargo --version` works in your terminal, then retry.");
    process.exit(1);
  }

  process.env[pathKey] = path;
  process.env.CARGO_HOME = cargoHome;
  process.env.RUSTUP_HOME = rustupHome;
}

try {
  const args = process.argv.slice(2);
  const nativeMac = process.platform === "darwin" && process.arch === "arm64";
  if (nativeMac && args[0] === "dev" && !process.env.AI_BLOGGER_PYTHON) {
    const stagedPython = join(root, "apps/desktop/src-tauri/.macos-build/studio-runtime/python/bin/python3.12");
    if (existsSync(stagedPython)) process.env.AI_BLOGGER_PYTHON = stagedPython;
  }
  if (nativeMac && args[0] === "dev" && !process.env.AI_BLOGGER_ZONOS_BIN_DIR) {
    const bin = join(root, "apps/desktop/src-tauri/.macos-build/zonos-native/bin");
    if (existsSync(join(bin, "zonos3-cli")) && existsSync(join(bin, "spk-encoder-cli"))) {
      process.env.AI_BLOGGER_ZONOS_BIN_DIR = bin;
    }
  }
  if (args[0] === "build-macos" || (nativeMac && args[0] === "build" && !args.includes("--debug"))) {
    if (!nativeMac) throw new Error("Build the Apple Silicon app on an arm64 macOS host.");
    const prepared = spawnSync("/usr/bin/python3", ["-B", join(root, "apps/desktop/scripts/prepare-macos-runtime.py")], {
      cwd: root,
      stdio: "inherit",
    });
    if (prepared.error) throw prepared.error;
    if (prepared.status !== 0) process.exit(prepared.status ?? 1);
    args[0] = "build";
    args.push("--config", JSON.stringify({ bundle: { resources: {
      ".macos-build/studio-runtime": "studio-runtime",
    } } }));
  }
  await run(args, `npm run ${process.env.npm_lifecycle_event}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
