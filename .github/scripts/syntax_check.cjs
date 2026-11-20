'use strict';
// Parses (never runs) every tracked .js/.cjs/.mjs file and every inline <script> block in tracked
// .html pages, so a syntax error in a page without its own test still fails CI.
// Usage: node .github/scripts/syntax_check.cjs
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const tracked = execFileSync('git', ['-C', root, 'ls-files', '-z'], { encoding: 'utf8' })
  .split('\0').filter(Boolean);

const problems = [];
let checked = 0;

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length;
}

const skippedModules = [];

function parse(source, file, firstLine) {
  checked += 1;
  try {
    new vm.Script(source, { filename: file, lineOffset: firstLine - 1 });
  } catch (err) {
    problems.push(`${file}:${firstLine}: ${err.message}`);
  }
}

for (const file of tracked) {
  const full = path.join(root, file);
  const ext = path.extname(file).toLowerCase();
  if (ext === '.js' || ext === '.cjs') {
    const text = fs.readFileSync(full, 'utf8');
    // CommonJS files are wrapped by Node, so a top-level return is legal there.
    parse(ext === '.cjs' ? `(function(){${text}\n})` : text, file, 1, false);
  } else if (ext === '.mjs') {
    parse(fs.readFileSync(full, 'utf8'), file, 1, true);
  } else if (ext === '.html') {
    const html = fs.readFileSync(full, 'utf8');
    const re = /<script(\s[^>]*)?>([\s\S]*?)<\/script[^>]*>/gi;
    let m;
    while ((m = re.exec(html)) !== null) {
      const attrs = m[1] || '';
      if (/\ssrc\s*=/i.test(attrs)) continue;
      const type = (/\stype\s*=\s*["']?([^"'\s>]+)/i.exec(attrs) || [])[1];
      if (type && !/^(text\/javascript|module)$/i.test(type)) continue; // JSON, templates
      const bodyStart = m.index + m[0].indexOf('>') + 1;
      if (type && type.toLowerCase() === 'module') { skippedModules.push(`${file}:${lineOf(html, bodyStart)}`); continue; }
      parse(m[2], file, lineOf(html, bodyStart));
    }
  }
}

for (const p of problems) console.log(`::error::${p}`);
for (const m of skippedModules) console.log(`::warning::${m}: ES module not syntax-checked`);
console.log(`Parsed ${checked} scripts: ${problems.length} syntax error(s).`);
process.exit(problems.length ? 1 : 0);
