# Telegram Mini App 视频下载助手

[English](README.en.md)

Windows 上的 Telegram Desktop 辅助工具：在 Telegram 中观看受支持的小程序视频时，在独立窗口查看最近识别的视频，按需加入下载队列。项目是非官方工具，与 Telegram 或小程序运营方无隶属关系。请只保存你有权下载的内容。

目前的下载功能针对 `ai_tiktoporn` 小程序的 MP4 视频；命令行监听还可识别其他 WebView 的视频请求，但不负责下载普通聊天视频、HLS 或 DASH。小程序可能预加载视频，因此「最近识别」不一定等于「正在播放」。

## 首次部署：从源码运行

已验证环境：Windows 11 x64、Python 3.12、Telegram Desktop。Windows 10 x64 也应适用，但尚未单独验证。Telegram 的小程序需要可用的 Microsoft Edge WebView2 Runtime。

1. 安装并登录 [Telegram Desktop](https://desktop.telegram.org/)，确认小程序在客户端内可以正常打开。
2. 下载或克隆本仓库，在项目目录打开 PowerShell。安装 [Python 3.12 x64](https://www.python.org/downloads/) 后运行：

   ```powershell
   python --version
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
   ```

3. **从系统托盘完全退出 Telegram**，再启动助手。监听参数只能在 Telegram 启动前设置：

   ```powershell
   .\.venv\Scripts\pythonw.exe .\telegram_video_gui.pyw
   ```

   可以为 `telegram_video_gui.pyw` 创建桌面快捷方式，并将快捷方式的程序设为 `.venv\Scripts\pythonw.exe`。使用 `pythonw.exe` 不会出现命令行窗口。

4. 助手会启动 Telegram。打开受支持的小程序并播放视频，在「最近识别」中核对标题，点击「下载」。任务会逐个进行；可以取消，之后重新下载时会尝试从 `.part` 文件续传。

窗口关闭后会留在系统托盘；从托盘菜单选择「退出」会停止监听和当前下载。EXE 默认保存到用户的 `Downloads/Telegram Videos`，源码运行默认保存到项目的 `downloads`，也可以在界面中更改。

如果 Telegram 已在运行且未开启监听，助手会提示从托盘完全退出 Telegram。退出后点击「重试连接」。若登录信息过期，在 Telegram 中重新打开小程序，再重试下载。

## 使用 EXE

如果你有可信来源提供的 Windows 构建包，可以直接运行 `TelegramVideoAssistant.exe`，无需安装 Python。**GitHub 源码仓库不包含 `dist/` 中的本地构建文件**；没有发布包时，请按上面的源码步骤部署，或自行打包。

开发者打包前请从托盘退出正在运行的助手，否则 Windows 会锁定旧 EXE。打包命令：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dev.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm .\TelegramVideoAssistant.spec
```

生成的 EXE 位于 `dist/TelegramVideoAssistant.exe`。打包配置会排除可能从构建机 `PATH` 误收集的第三方 ICU DLL，避免 QtCore 启动失败。

本项目的 MIT 许可证只覆盖本项目代码。若进一步发布 EXE，还需分别遵守 PySide6/Qt（LGPL/GPL 选项）、websocket-client（Apache-2.0）和 PyInstaller 的二进制分发条款；首次公开建议先发布源码。

## 命令行模式

按小程序链接下载单个视频：

```powershell
.\.venv\Scripts\python.exe .\download_telegram_video.py "https://t.me/ai_ym_lyf_bot/ai_tiktoporn?startapp=640"
```

小程序已经打开且登录信息仍有效时，可以添加 `--no-launch`。自动定位不到 Telegram WebView 的 History 数据库时，可以用 `--history-db "C:\path\to\Telegram Desktop\tdata\user_data\wvbots\EBWebView\Default\History"` 指定位置。下载文件默认保存在项目的 `downloads`，可用 `--output-dir` 更改。

仅监听 Telegram WebView 的视频请求：

```powershell
.\.venv\Scripts\python.exe .\download_telegram_video.py --monitor
```

监听会在终端显示请求地址，**默认不写入日志**。确需保存时显式指定：

```powershell
.\.venv\Scripts\python.exe .\download_telegram_video.py --monitor --monitor-log .\detected-videos.jsonl
```

日志可能包含临时签名地址和账户相关信息。不要把日志、下载的视频或 Telegram 数据目录提交到公开仓库。监听使用 `127.0.0.1:9222` 的 WebView2 调试端口；结束后完全退出 Telegram 才能关闭该端口。

## 验证与贡献

安装依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

GitHub Actions 会在 Windows 上运行同样的测试。贡献前请保持改动聚焦，并避免在问题、日志、截图和测试数据中包含真实的 `tgWebAppData`、签名视频地址或私人媒体。

首次公开前，请使用 Git 提交源码，**不要把整个工作目录拖入 GitHub 网页上传**，因为网页上传不会按 `.gitignore` 过滤文件。当前目录里的 `detected-videos.jsonl`、`downloads/` 和 `dist/` 都属于本地数据或构建产物，不应公开。运行 `git status --ignored` 和 `git ls-files`，确认它们没有被追踪，再推送仓库。

创建一个**空的** GitHub 仓库后，在项目目录执行以下步骤；如果目录已有 `.git`，跳过 `git init`。提交前请配置你自己的 Git 作者姓名和邮箱（可以使用 GitHub 提供的 `noreply` 邮箱）：

```powershell
git init -b main
git add .
git status --short
git ls-files
git commit -m "Prepare open-source release"
git remote add origin https://github.com/YOUR_NAME/telegram-video-downloader.git
git push -u origin main
```

若 Git 报 `Author identity unknown`，先在本仓库运行 `git config user.name "你的名字"` 和 `git config user.email "你的邮箱"`，再重试提交。将 `YOUR_NAME` 换成你的 GitHub 用户名，且务必在 `git ls-files` 中确认没有私人数据。

本项目的许可条款见 [LICENSE](LICENSE)。
