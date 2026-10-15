# Windows EXE 构建记录

产物：`dist/HappyHorseStudio.exe`，Windows x64 单文件 GUI 程序，包含 Python 运行时及整个 `web/` 目录，不包含 API Key、浏览器配置、任务历史或用户生成的视频。

构建环境：用户指定开发服务器上的独立临时 Docker 容器，Ubuntu 24.04 / Wine 9.0，官方 Python 3.13.16 Windows x64 embeddable runtime，PyPI PyInstaller 6.22.3。

构建命令（在 Windows Python 环境中执行）：

```text
python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name HappyHorseStudio --add-data web:web server.py
```

验证：

- Windows Python 环境的 9 项回归测试通过。
- 独立目录中只放 EXE，在无 Python 安装的全新 Wine 前缀运行成功。
- 校验 `/health`、HTML 定制标题、TokenPlan 选项、透明度滑块与退出按钮。
- 校验随包 JS / CSS / PNG 资源可访问，混用套餐 Key 与普通接口被拦截。
- 校验带本地令牌的退出请求停止后台服务。
- 下载后的 EXE SHA-256 与构建服务器一致。

SHA-256：`b88d023f78b69bfc907d999944c8533892e6cbf2ff12ddce28497d5a3347d25e`

验证范围为 Windows 兼容环境，尚未在 Windows 实机测试，也未使用真实 API Key 创建视频任务。程序未做 Authenticode 签名。

2026-10-15：恢复第一版浅色背景，删除界面定制文字、爱心和背景透明度选项。仅刷新 CArchive 中的 HTML/CSS/JS 并重算 PE 校验和；后端脚本、运行时和启动器条目逐字节保持不变。

本次更新验收通过：新版单文件 EXE 在无 Python 安装的兼容环境启动成功，实际提供的 HTML/CSS/JS 与工作区 SHA-256 一致，旧定制界面元素不存在，退出接口正常。
