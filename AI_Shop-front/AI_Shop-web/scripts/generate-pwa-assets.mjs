
import { copyFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(__dirname, '../public/pwa');
const publicDir = path.resolve(__dirname, '../public');

const LOGO = {
  cx: 16,
  cy: 16,
  width: 32,
  height: 32
};

const LOGO_PATHS = `
  <rect x="2" y="2" width="28" height="28" rx="7" fill="#0f766e"/>
  <path fill="none" stroke="#fff" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round" d="m8.8 16.2 4.7 4.7 9.7-10.1"/>
  <path fill="none" stroke="#bfdbfe" stroke-width="1.8" stroke-linecap="round" d="M9.4 7.1v4.2M7.3 9.2h4.2"/>
`;


const logoMarkSvg = (size, logoScale = 0.72) => {
  const scale = (size * logoScale) / LOGO.width;
  return `
<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
  <rect width="${size}" height="${size}" fill="#f4f7f8"/>
  <g transform="translate(${size / 2} ${size / 2}) scale(${scale}) translate(${-LOGO.cx} ${-LOGO.cy})">
    ${LOGO_PATHS}
  </g>
</svg>`;
};


const splashSvg = (width, height) => {
  const logoSize = Math.round(Math.min(width, height) * 0.18);
  const scale = (logoSize / LOGO.width).toFixed(4);
  const cx = width / 2;
  const cy = height * 0.46;
  return `
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <rect width="${width}" height="${height}" fill="#f4f7f8"/>
  <g transform="translate(${cx} ${cy}) scale(${scale}) translate(${-LOGO.cx} ${-LOGO.cy})">
    ${LOGO_PATHS}
  </g>
</svg>`;
};

const referenceWordmarkSvg = () => `
<svg xmlns="http://www.w3.org/2000/svg" width="188" height="95" viewBox="0 0 188 95">
  <g transform="translate(10 22) scale(1.55)">
    ${LOGO_PATHS}
  </g>
  <text x="68" y="45" fill="#17202a" font-size="25" font-weight="700" font-family="Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif">智选</text>
  <text x="68" y="65" fill="#0f766e" font-size="12" font-weight="600" font-family="Arial, sans-serif">SmartSelect</text>
</svg>`;

const SPLASH_SIZES = [
  { name: 'iphone-se', width: 750, height: 1334 },
  { name: 'iphone-12-13-14', width: 1170, height: 2532 },
  { name: 'iphone-14-pro', width: 1179, height: 2556 },
  { name: 'iphone-14-pro-max', width: 1290, height: 2796 },
  { name: 'iphone-16-pro', width: 1206, height: 2622 },
  { name: 'iphone-16-pro-max', width: 1320, height: 2868 }
];

const STARTUP_LINKS = [
  {
    href: '/pwa/splash-iphone-se.png',
    media:
      '(device-width: 375px) and (device-height: 667px) and (-webkit-device-pixel-ratio: 2) and (orientation: portrait)'
  },
  {
    href: '/pwa/splash-iphone-12-13-14.png',
    media:
      '(device-width: 390px) and (device-height: 844px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)'
  },
  {
    href: '/pwa/splash-iphone-14-pro.png',
    media:
      '(device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)'
  },
  {
    href: '/pwa/splash-iphone-14-pro-max.png',
    media:
      '(device-width: 430px) and (device-height: 932px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)'
  },
  {
    href: '/pwa/splash-iphone-16-pro.png',
    media:
      '(device-width: 402px) and (device-height: 874px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)'
  },
  {
    href: '/pwa/splash-iphone-16-pro-max.png',
    media:
      '(device-width: 440px) and (device-height: 956px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)'
  }
];

async function writePngFromSvg(file, svg, width, height) {
  await sharp(Buffer.from(svg), { density: 300 })
    .resize(width, height, { kernel: sharp.kernel.lanczos3 })
    .png({ compressionLevel: 9 })
    .toFile(file);
  console.log('wrote', file);
}

async function writeIconPng(name, width, height) {
  const master = 1024;
  const masterSvg = logoMarkSvg(master, 0.72);
  const file = path.join(outDir, name);
  await sharp(Buffer.from(masterSvg), { density: 300 })
    .resize(width, height, { kernel: sharp.kernel.lanczos3 })
    .png({ compressionLevel: 9 })
    .toFile(file);
  console.log('wrote', file);
}

async function main() {
  await mkdir(outDir, { recursive: true });

  await writeIconPng('apple-touch-icon.png', 180, 180);
  await writeIconPng('apple-touch-icon-1024.png', 1024, 1024);
  await writeIconPng('icon-192.png', 192, 192);
  await writeIconPng('icon-512.png', 512, 512);

  
  await copyFile(
    path.join(outDir, 'apple-touch-icon.png'),
    path.join(publicDir, 'apple-touch-icon.png')
  );
  console.log('wrote', path.join(publicDir, 'apple-touch-icon.png'));

  await writePngFromSvg(
    path.join(publicDir, 'simlect-origin/images/simlect-logo.png'),
    referenceWordmarkSvg(),
    188,
    95
  );

  for (const item of SPLASH_SIZES) {
    await writePngFromSvg(
      path.join(outDir, `splash-${item.name}.png`),
      splashSvg(item.width, item.height),
      item.width,
      item.height
    );
  }

  const linksSnippet = [
    ...STARTUP_LINKS.map(
      (item) =>
        `    <link rel="apple-touch-startup-image" media="${item.media}" href="${item.href}" />`
    ),
    '    <link rel="apple-touch-startup-image" href="/pwa/splash-iphone-16-pro-max.png" />'
  ].join('\n');

  await writeFile(
    path.join(outDir, 'startup-links.html'),
    `<!-- generated by scripts/generate-pwa-assets.mjs -->\n${linksSnippet}\n`
  );

  console.log('Done.');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
