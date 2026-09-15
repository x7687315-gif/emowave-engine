# -*- coding: utf-8 -*-
"""从 ui-draft-v1.html 抽出单个窗口稿，逐个用 Chrome headless 截图。
产出 docs/screenshots/*.png，供 GitHub README 使用。
临时 HTML 用完即删，不污染草稿本体。
"""
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "ui-draft-v1.html")
OUT = os.path.join(ROOT, "screenshots")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# (输出文件名, 窗口稿序号从 0 起, 说明)
TARGETS = [
    ("01-main.png", 0, "01 主界面 · 参数说明展开态"),
    ("02-drawer.png", 1, "02 右侧抽屉展开"),
    ("03-correct-sheet.png", 2, "03 纠正上滑框"),
]

INJECT = """
<style>
  html,body{margin:0!important;padding:0!important;background:#DED8CC!important}
  .stage{max-width:none!important;margin:0!important}
  .stage>*{display:none!important}
  .stage>.shot{display:flex!important;margin:46px auto 0!important}
  *{animation:none!important;transition:none!important}
</style>
"""


def build(idx: int) -> str:
    html = open(SRC, encoding="utf-8").read()
    # 给第 idx 个 <div class="win..."> 打上 shot 标记
    matches = list(re.finditer(r'<div class="win', html))
    if idx >= len(matches):
        raise SystemExit(f"只找到 {len(matches)} 个窗口稿，取不到第 {idx} 个")
    m = matches[idx]
    html = html[: m.end()] + " shot" + html[m.end() :]
    html = html.replace("</head>", INJECT + "</head>")
    return html


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, idx, desc in TARGETS:
        tmp = os.path.join(OUT, "_tmp_" + name.replace(".png", ".html"))
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(build(idx))
        dst = os.path.join(OUT, name)
        cmd = [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=2", "--window-size=1252,840",
            "--virtual-time-budget=3000",
            f"--screenshot={dst}", "file:///" + tmp.replace("\\", "/"),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if os.path.exists(dst):
            print(f"OK  {name:26s} {os.path.getsize(dst)/1024:7.1f} KB  {desc}")
        else:
            print(f"FAIL {name}\n{r.stdout}\n{r.stderr}")
        os.remove(tmp)
    print("输出目录:", OUT)


if __name__ == "__main__":
    main()
