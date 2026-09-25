# Telegram Mini App Video Assistant

[简体中文](README.md)

A Windows companion for Telegram Desktop. Watch supported Mini App videos in Telegram, review recently detected videos in a separate window, and choose which ones to download. This is an unofficial project and is not affiliated with Telegram or the Mini App operator. Download only content you have the right to save.

Downloads currently support MP4 videos from the `ai_tiktoporn` Mini App. The command-line monitor can detect video requests from other WebViews, but this tool does not download native Telegram chat videos, HLS, or DASH streams. Mini Apps may preload videos, so “Recently detected” does not necessarily mean “Currently playing.”

## First-time setup: run from source

Verified on Windows 11 x64 with Python 3.12 and Telegram Desktop. Windows 10 x64 should also work, but has not been tested separately. Telegram Mini Apps require a working Microsoft Edge WebView2 Runtime.

1. Install and sign in to [Telegram Desktop](https://desktop.telegram.org/). Check that the Mini App opens in Telegram.
2. Download or clone this repository and open PowerShell in its directory. Install [Python 3.12 x64](https://www.python.org/downloads/), then run:

   ```powershell
   python --version
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
   ```

3. **Exit Telegram completely, including its system tray icon**, before starting the assistant. WebView monitoring flags must be set before Telegram starts:

   ```powershell
   .\.venv\Scripts\pythonw.exe .\telegram_video_gui.pyw
   ```

   You can create a desktop shortcut for `telegram_video_gui.pyw` using `.venv\Scripts\pythonw.exe` as the program. `pythonw.exe` does not open a console window.

4. The assistant starts Telegram. Open the supported Mini App and play a video. Check its title under “Recently detected,” then click “Download.” Jobs run one at a time. You can cancel a job and later retry it; a `.part` file allows resuming when the server supports it.

Closing the window leaves the assistant in the system tray. Choosing “Exit” from the tray stops monitoring and the current download. The EXE saves to the user's `Downloads/Telegram Videos` by default; source runs save to the repository's `downloads` folder. The location can be changed in the UI.

If Telegram is already running without monitoring enabled, exit it completely from the tray and click “Retry connection.” If authentication expires, reopen the Mini App in Telegram and retry the download.

## Using an EXE

If you have a Windows build from a source you trust, run `TelegramVideoAssistant.exe` directly without installing Python. **The GitHub source repository does not contain the local `dist/` build.** If no release is available, run from source as above or build your own EXE.

Exit any running assistant from its tray icon before rebuilding, or Windows will lock the old EXE. Build commands for developers:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dev.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm .\TelegramVideoAssistant.spec
```

The output is `dist/TelegramVideoAssistant.exe`. The build spec excludes incompatible ICU DLLs that PyInstaller might pick up from the builder's `PATH`, which would prevent QtCore from loading.

This project's MIT license covers its own code. If you distribute an EXE, also comply with the binary distribution terms for PySide6/Qt (LGPL/GPL options), websocket-client (Apache-2.0), and PyInstaller. Publishing source first is the simplest initial release.

## Command-line mode

Download one video from a Mini App link:

```powershell
.\.venv\Scripts\python.exe .\download_telegram_video.py "https://t.me/ai_ym_lyf_bot/ai_tiktoporn?startapp=640"
```

Use `--no-launch` when the Mini App is already open and its authentication data is still valid. If automatic WebView History discovery fails, pass `--history-db "C:\path\to\Telegram Desktop\tdata\user_data\wvbots\EBWebView\Default\History"`. Files go to the repository's `downloads` directory by default; use `--output-dir` to change it.

Monitor Telegram WebView video requests without downloading:

```powershell
.\.venv\Scripts\python.exe .\download_telegram_video.py --monitor
```

The monitor prints URLs to the terminal and **does not save a log by default**. To save one explicitly:

```powershell
.\.venv\Scripts\python.exe .\download_telegram_video.py --monitor --monitor-log .\detected-videos.jsonl
```

Logs may contain temporary signed URLs and account-related data. Never commit logs, downloaded media, or Telegram profile data to a public repository. Monitoring uses a WebView2 debugging port at `127.0.0.1:9222`; exit Telegram completely afterward to close it.

## Verification and contributions

After installing dependencies, run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

GitHub Actions runs the same tests on Windows. Keep contributions focused, and do not include real `tgWebAppData`, signed media URLs, or private videos in issues, logs, screenshots, or test fixtures.

Before publishing for the first time, use Git to add the source files. **Do not drag the entire working directory into GitHub's web uploader:** it does not apply `.gitignore`. The existing `detected-videos.jsonl`, `downloads/`, and `dist/` are private data or build output. Check `git status --ignored` and `git ls-files` to ensure none is tracked before pushing.

Create an **empty** GitHub repository, then run the following in this directory. Skip `git init` if `.git` already exists. Configure your own Git author name and email before committing; GitHub's `noreply` email is an option:

```powershell
git init -b main
git add .
git status --short
git ls-files
git commit -m "Prepare open-source release"
git remote add origin https://github.com/YOUR_NAME/telegram-video-downloader.git
git push -u origin main
```

If Git reports `Author identity unknown`, run `git config user.name "Your Name"` and `git config user.email "you@example.com"` in this repository before retrying the commit. Replace `YOUR_NAME` with your GitHub username and confirm `git ls-files` contains no private data.

See [LICENSE](LICENSE) for the license terms.
