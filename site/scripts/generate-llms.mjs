// Generates llms.txt and llms-full.txt from the docs/*.md source files
// at build time, so they're always in sync with the documentation.
//
// llms.txt:       curated index following the llms.txt v2 spec
// llms-full.txt:  all docs concatenated as Markdown for offline reading
//
// Run via: node scripts/generate-llms.mjs (before astro build)
// Also wired into the build script in package.json.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const docsDir = join(root, '..', 'docs');
const publicDir = join(root, 'public');

const PAGES_URL = 'https://yorch.github.io/harness-evaluator';

// Ordered list matching the sidebar order in astro.config.mjs
const PAGES = [
  'index',
  'getting-started',
  'architecture',
  'gateway-proxy',
  'orchestrator',
  'docker-runner',
  'evaluators',
  'adapters',
  'cli-reference',
  'configuration',
  'reporting',
  'statistics',
  'development',
];

// Human-readable titles for the llms.txt link list
const TITLES = {
  'index': 'Documentation Overview',
  'getting-started': 'Getting Started',
  'architecture': 'Architecture',
  'gateway-proxy': 'Gateway Proxy',
  'orchestrator': 'Orchestrator',
  'docker-runner': 'Docker Runner',
  'evaluators': 'Evaluators',
  'adapters': 'Adapters',
  'cli-reference': 'CLI Reference',
  'configuration': 'Configuration',
  'reporting': 'Reporting',
  'statistics': 'Statistics',
  'development': 'Development',
};

// Short descriptions for the llms.txt link list
const DESCRIPTIONS = {
  'index': 'Overview, design goals, and project structure',
  'getting-started': 'Install from PyPI, pull the Docker image, set API keys, and run your first evaluation — no clone required',
  'architecture': 'Component map and data flow from config to results',
  'gateway-proxy': 'HTTP/SSE proxy for token/cost/latency accounting and provider call capture',
  'orchestrator': 'Matrix builder, budget engine, retry logic, and resumability',
  'docker-runner': 'Container lifecycle, isolation, and credential mounting',
  'evaluators': 'SWE-bench-style hidden tests and open-ended LLM judge track',
  'adapters': 'Per-harness CLI wrappers and observability tiers',
  'cli-reference': 'Full command reference for the harness-evaluator CLI',
  'configuration': 'YAML config format, auth modes, environment variables, and model specs',
  'reporting': 'Static HTML/JSON/CSV report generation and CSV sanitization',
  'statistics': 'Mixed-effects models, variance decomposition, and bootstrap CIs',
  'development': 'Contributing, testing, linting, and release process',
};

function stripFrontmatter(text) {
  return text.replace(/^---\n.*?\n---\n/s, '');
}

function generateLlmsTxt() {
  const lines = [];

  lines.push('# harness-evaluator');
  lines.push('');
  lines.push('> harness-evaluator compares agentic coding harnesses (Claude Code, Codex, Pi, OpenCode, OMP) against one or more models on a set of tasks, measuring token efficiency, task effectiveness, time efficiency, and cost. It routes all provider traffic through a gateway proxy for accurate token/cost accounting, runs harnesses in isolated Docker containers, and produces static reports plus an interactive dashboard. Results are stored in SQLite and analyzed with mixed-effects statistical models.');
  lines.push('');
  lines.push('harness-evaluator is a Python CLI tool that orchestrates Node.js coding harnesses running inside Docker containers. All provider API traffic is routed through a custom aiohttp gateway proxy that captures token usage, cost, and latency. The evaluator supports both SWE-bench-style coding tasks (with hidden tests and partial credit) and open-ended tasks (with an LLM judge and rubric scoring).');
  lines.push('');

  // Documentation section
  lines.push('## Documentation');
  lines.push('');

  for (const name of PAGES) {
    if (name === 'index') continue; // skip index in the link list
    const title = TITLES[name] || name;
    const desc = DESCRIPTIONS[name] || '';
    const url = `${PAGES_URL}/docs/${name}/`;
    lines.push(`- [${title}](${url}): ${desc}`);
  }
  lines.push('');

  // Code section
  lines.push('## Code');
  lines.push('');
  lines.push(`- [GitHub Repository](https://github.com/yorch/harness-evaluator): Source code, issues, and releases`);
  lines.push(`- [PyPI Package](https://pypi.org/project/harness-evaluator/): Install with \`uvx harness-evaluator\` or \`pip install harness-evaluator\``);
  lines.push(`- [Docker Image](https://github.com/yorch/harness-evaluator/pkgs/container/harness-evaluator-runner): Runner image with all 5 harnesses pre-installed`);
  lines.push('');

  // Optional section
  lines.push('## Optional');
  lines.push('');
  lines.push(`- [Full Documentation (single file)](${PAGES_URL}/llms-full.txt): All documentation pages concatenated as Markdown for offline reading`);
  lines.push(`- [License](https://github.com/yorch/harness-evaluator/blob/main/LICENSE): MIT License`);

  return lines.join('\n') + '\n';
}

function generateLlmsFull() {
  const parts = [];

  parts.push('# harness-evaluator — Full Documentation');
  parts.push('');
  parts.push('> Complete documentation for harness-evaluator, a tool that compares agentic coding harnesses (Claude Code, Codex, Pi, OpenCode, OMP) on token efficiency, task effectiveness, time efficiency, and cost.');
  parts.push('');
  parts.push(`Source: ${PAGES_URL}/`);
  parts.push('');

  for (let i = 0; i < PAGES.length; i++) {
    const name = PAGES[i];
    const file = join(docsDir, `${name}.md`);
    if (!existsSync(file)) continue;

    const text = stripFrontmatter(readFileSync(file, 'utf-8'));

    if (i > 0) {
      parts.push('');
      parts.push('---');
      parts.push('');
    }
    parts.push(text.trimEnd());
  }

  return parts.join('\n') + '\n';
}

// --- Main ---

if (!existsSync(docsDir)) {
  console.error(`docs/ directory not found at ${docsDir}`);
  process.exit(1);
}

const llmsTxt = generateLlmsTxt();
const llmsFull = generateLlmsFull();

writeFileSync(join(publicDir, 'llms.txt'), llmsTxt);
writeFileSync(join(publicDir, 'llms-full.txt'), llmsFull);

console.log(`Generated llms.txt (${(llmsTxt.length / 1024).toFixed(1)} KB)`);
console.log(`Generated llms-full.txt (${(llmsFull.length / 1024).toFixed(1)} KB)`);
console.log(`Pages included: ${PAGES.length}`);
