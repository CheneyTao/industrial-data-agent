/* Pure renderer test: does not open a browser, execute HTML, or access the network. */
const fs = require('node:fs');
const path = require('node:path');
const [directory, modules] = process.argv.slice(2);
if (!directory || !modules) throw new Error('Usage: node verify_echarts_ssr.cjs evidence-directory node_modules');
const echarts = require(path.join(modules, 'echarts'));
const proof = JSON.parse(fs.readFileSync(path.join(directory, 'evidence.json'), 'utf8'));
const output = path.join(directory, 'rendered-charts');
fs.mkdirSync(output, {recursive: true});
const results = [];
for (const artifact of proof.artifacts.filter(a => a.kind === 'chart')) {
  const option = artifact.content.option;
  const panelCount = Array.isArray(option.grid) ? option.grid.length : 1;
  const chart = echarts.init(null, null, {renderer: 'svg', ssr: true, width: 1100, height: Math.max(460, panelCount * 250)});
  chart.setOption(option);
  const svg = chart.renderToSVGString();
  if (!svg.includes('<svg') || !svg.includes('<path')) throw new Error('No rendered paths');
  const filename = artifact.artifactId + '.svg';
  fs.writeFileSync(path.join(output, filename), svg);
  results.push({artifactId: artifact.artifactId, type: artifact.content.spec.type, panels: panelCount, bytes: Buffer.byteLength(svg), file: filename});
  chart.dispose();
}
fs.writeFileSync(path.join(directory, 'renderer_check.json'), JSON.stringify({engine: 'ECharts SVG SSR', browserVerified: false, results}, null, 2));
process.stdout.write(JSON.stringify({rendered: results.length, results}) + '\n');
