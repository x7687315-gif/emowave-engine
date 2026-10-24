# 发布包

## v3.0.0 · 2026-10-23

**下载**：https://github.com/x7687315-gif/emowave-engine/releases/download/v3.0.0/EmoWave-3.0.0-win64.zip

| 资产 | 说明 |
|---|---|
| `EmoWave-3.0.0-win64.zip` | Windows 64 位便携版（单文件 EXE + 使用说明），约 36 MB |
| `emowave-3.0.0-py3-none-any.whl` | Python 包（内核，不含 GUI） |
| `emowave-3.0.0.tar.gz` | Python 源码包 |

> 二进制不再放进 git 仓库：它们挂在 GitHub Release `v3.0.0` 上。

**SHA256**

```
ccfc420dd99b2c202e0e1e02ddf5f9a2972314ba7d6f926e17b64c36a9f85e4a
```

**使用**：解压后双击 `EmoWave.exe`。无需安装 Python，不写注册表。
数据在 `%USERPROFILE%\.emowave\emowave.db`，卸载 = 删除程序目录（数据另删）。

**构建方式**（可复现）：

```bash
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name EmoWave --icon docs/emowave.ico main_app.py
```

解释器：Python 3.9.13 · PyQt5 5.15.11 · PyInstaller 6.21.0

**未做**：代码签名。SmartScreen 可能提示「更多信息 → 仍要运行」。
若需要正式分发，建议购买证书后对 EXE 签名，再改用 GitHub Releases 托管（仓库内保留二进制会持续增大克隆体积）。
