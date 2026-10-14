"""Package installed frontend renderers with an exported report; no CDN required."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def offline_report_html(content: str, output: Path, modules: Path) -> str:
    assets = output / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for package, relative, target in [
        ("markdown-it", "dist/markdown-it.min.js", "markdown-it.min.js"),
        ("dompurify", "dist/purify.min.js", "purify.min.js"),
        ("echarts", "dist/echarts.min.js", "echarts.min.js"),
    ]:
        shutil.copy2(modules / package / relative, assets / target)
        for license_name in ("LICENSE", "LICENSE.txt"):
            license_path = modules / package / license_name
            if license_path.is_file():
                shutil.copy2(license_path, assets / f"{package}-{license_name}")
    raw = json.dumps(content, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    return (
        """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DataAgent 可追溯分析报告</title>
<script src="assets/markdown-it.min.js"></script>
<script src="assets/purify.min.js"></script>
<script src="assets/echarts.min.js"></script>
<style>
body{margin:0;background:#f3f6fb;color:#263247;font:16px/1.7 "Microsoft YaHei",sans-serif}
main{max-width:1100px;margin:30px auto;padding:32px 44px;background:white;border-radius:14px}
h1,h2,h3{color:#173660}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border:1px solid #dbe3ef;padding:8px}th{background:#edf3fd;text-align:left}
pre{padding:16px;background:#101d35;color:#e4eefb;overflow:auto;border-radius:8px}
code{overflow-wrap:anywhere}.chart{width:100%;height:460px;margin:24px 0}
@media(max-width:700px){main{padding:18px;margin:8px}.chart{height:420px}}
@media print{body{background:white}main{padding:0;margin:0;max-width:none}}
</style></head><body><main id="report"></main>
<script type="application/json" id="raw">"""
        + raw
        + """</script>
<script>
(() => {
  const root=document.getElementById('report');
  const raw=JSON.parse(document.getElementById('raw').textContent);
  if (!window.markdownit || !window.DOMPurify || !window.echarts) {
    root.style.whiteSpace='pre-wrap'; root.textContent=raw; return;
  }
  const md=markdownit({html:false,linkify:false});
  const fence=md.renderer.rules.fence;
  md.renderer.rules.fence=(tokens,index,options,env,self) => {
    const token=tokens[index];
    if (token.info.trim()==='echarts') {
      return '<div class="chart" data-option="'+encodeURIComponent(token.content)+'"></div>';
    }
    return fence(tokens,index,options,env,self);
  };
  root.innerHTML=DOMPurify.sanitize(md.render(raw),{USE_PROFILES:{html:true}});
  root.querySelectorAll('.chart').forEach(node => {
    try {
      const chart=echarts.init(node);
      chart.setOption(JSON.parse(decodeURIComponent(node.dataset.option)));
      window.addEventListener('resize',()=>chart.resize());
    } catch(error) {node.textContent='图表渲染失败：'+String(error);}
  });
})();
</script></body></html>"""
    )
