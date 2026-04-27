/**
 * Generate PWA and Tauri icons from the canonical marketing favicon.svg
 * Usage: node scripts/gen-icons.mjs
 */
import sharp from 'sharp';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');

const svgPath = resolve(root, 'sites/marketing/public/favicon.svg');
const svg = readFileSync(svgPath);

const targets = [
  // Web app favicon (replace the old one)
  { out: 'web/public/favicon.svg', copy: true },
  // PWA icons
  { out: 'web/public/pwa-192.png',          size: 192 },
  { out: 'web/public/pwa-512.png',          size: 512 },
  { out: 'web/public/pwa-maskable-512.png', size: 512 },
  // Tauri desktop icon
  { out: 'src-tauri/icons/icon.png',        size: 512 },
];

for (const t of targets) {
  const outPath = resolve(root, t.out);
  if (t.copy) {
    // just copy the SVG
    readFileSync(svgPath); // already read above — write it
    import('fs').then(({ writeFileSync }) => writeFileSync(outPath, svg));
    console.log(`  copied  ${t.out}`);
    continue;
  }
  await sharp(svg, { density: Math.round(t.size * 72 / 64) })
    .resize(t.size, t.size)
    .png()
    .toFile(outPath);
  console.log(`  ${t.size}x${t.size}  →  ${t.out}`);
}

console.log('\nDone.');
