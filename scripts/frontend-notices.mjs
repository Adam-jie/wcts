import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const frontend = join(project, 'frontend');
const root = JSON.parse(await readFile(join(frontend, 'package.json'), 'utf8'));
const seen = new Set();
const records = [];

async function collect(name, parent) {
  const require = createRequire(join(parent, 'package.json'));
  let entry;
  try { entry = require.resolve(`${name}/package.json`); }
  catch { try { entry = require.resolve(name); } catch { return; } }
  let directory = dirname(entry), pkg;
  while (true) {
    try {
      const candidate = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
      if (candidate.name === name) { pkg = candidate; break; }
    } catch {}
    const next = dirname(directory);
    if (next === directory) throw new Error(`Cannot locate installed metadata for ${name}`);
    directory = next;
  }
  const key = `${name}@${pkg.version}`;
  if (seen.has(key)) return;
  seen.add(key);
  const notices = [];
  for (const file of await readdir(directory, { withFileTypes: true })) {
    if (file.isFile() && /^(licen[sc]e|copying|notice)([._-].*)?$/i.test(file.name)) {
      notices.push(`${file.name}\n${await readFile(join(directory, file.name), 'utf8')}`);
    }
  }
  records.push({ key, license: pkg.license || 'See package notices', notices });
  for (const dependency of Object.keys({ ...pkg.dependencies, ...pkg.optionalDependencies })) await collect(dependency, directory);
}

for (const name of Object.keys(root.dependencies)) await collect(name, frontend);
records.sort((a, b) => a.key.localeCompare(b.key));
const header = 'TestFix hardware workbench — third-party notices\n\n'
  + 'Design references: https://github.com/shadcn-ui/ui and https://reactflow.dev/examples/overview\n'
  + 'The dashboard layout is original application code; no commercial Pro examples or sample business data are included.\n\n';
const text = header + records.map(({ key, license, notices }) => `${'='.repeat(72)}\n${key} — ${license}\n\n${notices.join('\n\n') || 'No standalone notice file shipped in this package; see package metadata.'}\n`).join('\n');
await mkdir(join(frontend, 'public'), { recursive: true });
await writeFile(join(frontend, 'public/THIRD_PARTY_NOTICES.txt'), text, 'utf8');
console.log(`Preserved license notices for ${records.length} installed runtime packages.`);
