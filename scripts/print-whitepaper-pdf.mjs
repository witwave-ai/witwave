#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import puppeteer from 'puppeteer-core';

const [, , htmlPath, pdfPath] = process.argv;
const chromePath = process.env.CHROME;

if (!htmlPath || !pdfPath) {
  console.error('Usage: CHROME=/path/to/chrome scripts/print-whitepaper-pdf.mjs <input.html> <output.pdf>');
  process.exit(2);
}

if (!chromePath) {
  console.error('CHROME must point to a Chrome or Chromium executable.');
  process.exit(2);
}

if (!fs.existsSync(htmlPath)) {
  console.error(`HTML input not found: ${htmlPath}`);
  process.exit(1);
}

fs.mkdirSync(path.dirname(pdfPath), { recursive: true });

const browser = await puppeteer.launch({
  executablePath: chromePath,
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(path.resolve(htmlPath)).href, { waitUntil: 'networkidle0' });
  await page.emulateMediaType('print');
  await page.pdf({
    path: pdfPath,
    format: 'Letter',
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: `
      <div style="width: 100%; padding: 0 0.7in; color: #6b7280; font: 8px Arial, sans-serif; display: flex; justify-content: flex-end;">
        <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
      </div>
    `,
    margin: {
      top: '0.72in',
      right: '0.72in',
      bottom: '0.78in',
      left: '0.72in',
    },
  });
} finally {
  await browser.close();
}
