import { gzipSync } from 'node:zlib';
import { readFile, readdir } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../dist', import.meta.url));
const html = await readFile(join(root, 'index.html'), 'utf8');
const entryMatches = [...html.matchAll(/<script[^>]+type="module"[^>]+src="([^"]+)"/g)];
const entryNames = new Set(
  entryMatches
    .map((match) => decodeURIComponent(match[1]).replace(/^\/+/, ''))
    .filter((name) => name.endsWith('.js'))
    .map((name) => name.split('/').pop())
);

async function collectJs(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) result.push(...(await collectJs(path)));
    else if (entry.name.endsWith('.js')) result.push(path);
  }
  return result;
}

const files = await collectJs(root);
const violations = [];
for (const file of files) {
  const name = relative(root, file);
  const source = await readFile(file);
  const gzipBytes = gzipSync(source, { level: 9 }).byteLength;
  const limit = entryNames.has(name.split('/').pop()) ? 300 * 1024 : 400 * 1024;
  if (gzipBytes > limit) {
    violations.push(`${name}: ${(gzipBytes / 1024).toFixed(1)} KiB > ${(limit / 1024).toFixed(0)} KiB`);
  }
}

if (violations.length) {
  console.error('Bundle budget exceeded:\n' + violations.join('\n'));
  process.exit(1);
}

console.log(`Bundle budget passed (${files.length} JavaScript assets checked).`);
