import { spawnSync } from "node:child_process";
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(scriptDir, "..");
const manifestPath = path.join(appRoot, "palette-plugin.json");
const packagePath = path.join(appRoot, "package.json");

const args = process.argv.slice(2);
const envArg = readArg("--env") ?? "staging";
const dryRun = args.includes("--dry-run");
const nonInteractiveBump = readArg("--bump");

if (!["staging", "production"].includes(envArg)) {
  fail(`Invalid --env "${envArg}". Expected "staging" or "production".`);
}

if (nonInteractiveBump && !["major", "minor", "update"].includes(nonInteractiveBump)) {
  fail(`Invalid --bump "${nonInteractiveBump}". Expected "major", "minor", or "update".`);
}

const manifest = await readJson(manifestPath);
const packageJson = await readJson(packagePath);
const currentVersion = manifest.version;

assertValidVersion(currentVersion, "palette-plugin.json");

if (packageJson.version !== currentVersion) {
  fail(
    `Version mismatch: palette-plugin.json has ${currentVersion}, package.json has ${packageJson.version}. ` +
      "Fix this before publishing."
  );
}

const bumpType = nonInteractiveBump ?? (await promptForBump(currentVersion));
const nextVersion = bumpVersion(currentVersion, bumpType);

console.log(`Bumping ${currentVersion} -> ${nextVersion} (${bumpType})`);
console.log(`Publish environment: ${envArg}`);

manifest.version = nextVersion;
packageJson.version = nextVersion;

if (dryRun) {
  console.log("Dry run enabled; no files were changed and publish commands were not run.");
  process.exit(0);
}

await writeJson(manifestPath, manifest);
await writeJson(packagePath, packageJson);

runCli(["test"]);
runCli(["package"]);

const publishArgs = ["publish", "--env", envArg];
if (envArg === "production") {
  publishArgs.push("-y");
}
runCli(publishArgs);

console.log(`Published ${manifest.id} ${nextVersion} to ${envArg}.`);

function readArg(name) {
  const index = args.indexOf(name);
  if (index === -1) return undefined;
  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    fail(`Missing value for ${name}.`);
  }
  return value;
}

async function promptForBump(version) {
  const rl = createInterface({ input, output });
  try {
    while (true) {
      const answer = (
        await rl.question(
          `Current version is ${version}. Select version bump (major/minor/update): `
        )
      )
        .trim()
        .toLowerCase();

      if (["major", "minor", "update"].includes(answer)) {
        return answer;
      }

      console.log('Please enter "major", "minor", or "update".');
    }
  } finally {
    rl.close();
  }
}

function bumpVersion(version, bumpType) {
  const [major, minor, patch] = parseVersion(version);

  if (bumpType === "major") return `${major + 1}.0.0`;
  if (bumpType === "minor") return `${major}.${minor + 1}.0`;
  return `${major}.${minor}.${patch + 1}`;
}

function parseVersion(version) {
  assertValidVersion(version, "version");
  return version.split(".").map((part) => Number.parseInt(part, 10));
}

function assertValidVersion(version, source) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    fail(`Invalid version in ${source}: "${version}". Expected X.Y.Z.`);
  }
}

async function readJson(filePath) {
  try {
    return JSON.parse(await readFile(filePath, "utf8"));
  } catch (error) {
    fail(`Failed to read ${filePath}: ${error.message}`);
  }
}

async function writeJson(filePath, value) {
  await writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

// Prefer the pltt on PATH over `npx @palettelab/cli@latest`: npx resolves a
// fresh copy into its own cache, so any local fix to the installed CLI (for
// example allowing a manifest key the published CLI does not know yet) is
// bypassed and the publish fails.
function runCli(cliArgs) {
  const installed = spawnSync("pltt", ["--version"], { stdio: "ignore", shell: false });
  if (!installed.error && installed.status === 0) {
    run("pltt", cliArgs);
    return;
  }
  run("npx", ["--yes", "@palettelab/cli@latest", ...cliArgs]);
}

function run(command, commandArgs) {
  console.log(`\n> ${command} ${commandArgs.join(" ")}`);
  const result = spawnSync(command, commandArgs, {
    cwd: appRoot,
    stdio: "inherit",
    shell: false
  });

  if (result.error) {
    fail(result.error.message);
  }

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function fail(message) {
  console.error(message);
  process.exit(1);
}
