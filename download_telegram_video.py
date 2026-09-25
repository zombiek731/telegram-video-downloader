#!/usr/bin/env python3
"""Download a video from the inspected Telegram Mini App link.

The script reuses short-lived Telegram Web App authentication data stored by
Telegram Desktop. Signed media URLs are saved only with --monitor-log.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


DEFAULT_LINK = "https://t.me/ai_ym_lyf_bot/ai_tiktoporn?startapp=640"
APP_BASE = "https://cdn.asset-7k2m.com/app"
CANONICAL_BOT = "lyf_ym_bot"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147 Safari/537.36"
)
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    ".m3u8",
    ".mpd",
}
VIDEO_MIME_TYPES = {
    "application/dash+xml",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
}


class TelegramAuthenticationError(RuntimeError):
    pass


class DownloadCancelled(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a video referenced by the ai_tiktoporn Telegram Mini App."
    )
    parser.add_argument("link", nargs="?", default=DEFAULT_LINK)
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "downloads",
    )
    parser.add_argument(
        "--history-db",
        type=Path,
        help="Path to Telegram Desktop WebView's Default/History database.",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Do not open the Mini App in Telegram before reading authentication data.",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Monitor video URLs requested by Telegram Desktop WebViews.",
    )
    parser.add_argument(
        "--monitor-log",
        type=Path,
        help="Optional JSON Lines file for --monitor; may contain signed video URLs.",
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=9222,
        help="Local WebView2 debugging port used by --monitor (default: 9222).",
    )
    parser.add_argument(
        "--dedupe-seconds",
        type=float,
        default=3.0,
        help="Suppress repeated range requests for the same URL (default: 3).",
    )
    parser.add_argument(
        "--no-launch-telegram",
        action="store_true",
        help="In monitor mode, require an existing debugging endpoint.",
    )
    return parser.parse_args()


def parse_link(link: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(link)
    parts = [part for part in parsed.path.split("/") if part]
    startapp = urllib.parse.parse_qs(parsed.query).get("startapp", [""])[0]
    if parsed.scheme != "https" or parsed.netloc.lower() != "t.me" or len(parts) != 2:
        raise ValueError("Expected https://t.me/<bot>/<app>?startapp=<video-id>")
    if not startapp:
        raise ValueError("The link is missing startapp=<video-id>")
    domain = CANONICAL_BOT if parts[0].lower() == "ai_ym_lyf_bot" else parts[0]
    deep_link = (
        "tg://resolve?"
        + urllib.parse.urlencode(
            {"domain": domain, "appname": parts[1], "startapp": startapp}
        )
    )
    return startapp, deep_link


def telegram_executable() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        key_path = r"Software\Classes\tg\shell\open\command"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            command = str(winreg.QueryValue(key, None))
        match = re.search(r'"([^"]*Telegram\.exe)"', command, re.IGNORECASE)
        return Path(match.group(1)) if match else None
    except OSError:
        return None


def find_history_db(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())

    executable = telegram_executable()
    if executable:
        candidates.append(
            executable.parent
            / "tdata"
            / "user_data"
            / "wvbots"
            / "EBWebView"
            / "Default"
            / "History"
        )

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            Path(appdata)
            / "Telegram Desktop"
            / "tdata"
            / "user_data"
            / "wvbots"
            / "EBWebView"
            / "Default"
            / "History"
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    checked = "\n  ".join(str(path) for path in candidates) or "(none)"
    raise FileNotFoundError(
        "Telegram Desktop WebView History was not found. Checked:\n  " + checked
    )


def recent_webapp_urls(history_db: Path, limit: int = 20) -> list[tuple[str, int]]:
    uri = history_db.resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True, timeout=2) as connection:
        rows = connection.execute(
            """
            SELECT url, last_visit_time
            FROM urls
            WHERE url LIKE ? AND url LIKE '%#tgWebAppData=%'
            ORDER BY last_visit_time DESC
            LIMIT ?
            """,
            (APP_BASE + "%", limit),
        ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def latest_webapp_url(history_db: Path) -> tuple[str, int] | None:
    recent = recent_webapp_urls(history_db, 1)
    return recent[0] if recent else None


def refresh_webapp_url(history_db: Path, deep_link: str) -> str:
    previous = latest_webapp_url(history_db)
    previous_visit = previous[1] if previous else -1

    if os.name != "nt":
        raise RuntimeError("Automatic Telegram launch is currently supported on Windows only")

    print("Opening the Mini App in Telegram Desktop...")
    os.startfile(deep_link)  # type: ignore[attr-defined]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        time.sleep(1)
        current = latest_webapp_url(history_db)
        if current and current[1] > previous_visit:
            return current[0]

    if previous:
        print("No new History entry detected; trying the latest existing login data.")
        return previous[0]
    raise RuntimeError("Open the Mini App in Telegram, then run this command again")


def init_data_from_url(webapp_url: str) -> str:
    fragment = urllib.parse.urlparse(webapp_url).fragment
    init_data = urllib.parse.parse_qs(fragment).get("tgWebAppData", [""])[0]
    if not init_data:
        raise ValueError("Telegram Web App authentication data is missing")
    return init_data


def request_json(url: str, init_data: str, referer: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Referer": referer,
            "User-Agent": USER_AGENT,
            "X-Tg-Init-Data": init_data,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise TelegramAuthenticationError(
                "Telegram authentication data was rejected; reopen the Mini App and retry"
            ) from error
        raise


def request_feed(pid: str, webapp_urls: list[str]) -> tuple[dict, str]:
    referer = APP_BASE + "?" + urllib.parse.urlencode({"pid": pid})
    feed_url = APP_BASE + "/api/feed?" + urllib.parse.urlencode(
        {"src": "one", "q": pid, "offset": 0, "n": 8}
    )
    authentication_error = None
    for webapp_url in webapp_urls:
        init_data = init_data_from_url(webapp_url)
        try:
            return request_json(feed_url, init_data, referer), init_data
        except TelegramAuthenticationError as error:
            authentication_error = error
    if authentication_error:
        raise authentication_error
    raise RuntimeError("Open this Mini App in Telegram Desktop first")


def safe_filename(title: str, pid: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "video"
    return f"{cleaned[:120]} [{pid}].mp4"


def supported_video_url(value: str) -> str:
    url = urllib.parse.urljoin(APP_BASE + "/", value)
    parsed = urllib.parse.urlsplit(url)
    app = urllib.parse.urlsplit(APP_BASE)
    if parsed.scheme != "https" or parsed.netloc.lower() != app.netloc.lower():
        raise ValueError("Video URL is outside the supported Mini App host")
    return url


def content_total(headers, existing: int) -> int | None:
    content_range = headers.get("Content-Range", "")
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    length = headers.get("Content-Length")
    return existing + int(length) if length else None


def download_video(
    url: str,
    destination: Path,
    referer: str,
    init_data: str,
    progress: Callable[[int, int | None, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    if cancel and cancel.is_set():
        raise DownloadCancelled("Download cancelled")
    part = destination.with_suffix(destination.suffix + ".part")
    existing = part.stat().st_size if part.exists() else 0
    headers = {
        "Referer": referer,
        "User-Agent": USER_AGENT,
        "X-Tg-Init-Data": init_data,
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"

    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        if error.code == 416 and existing:
            match = re.search(r"\*/(\d+)$", error.headers.get("Content-Range", ""))
            if match and existing == int(match.group(1)):
                part.replace(destination)
                return
        raise

    with response:
        if existing and response.status != 206:
            existing = 0
        mode = "ab" if existing else "wb"
        total = content_total(response.headers, existing)
        downloaded = existing
        started = time.monotonic()
        with part.open(mode) as output:
            while True:
                if cancel and cancel.is_set():
                    raise DownloadCancelled("Download cancelled")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                elapsed = max(time.monotonic() - started, 0.001)
                speed = (downloaded - existing) / elapsed / 1024 / 1024
                if progress:
                    progress(downloaded, total, speed)
                else:
                    if total:
                        percent = downloaded * 100 / total
                        description = f"{percent:6.2f}%  {downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MiB"
                    else:
                        description = f"{downloaded / 1024 / 1024:.1f} MiB"
                    print(f"\r{description}  {speed:.1f} MiB/s", end="", flush=True)

    if not progress:
        print()
    if cancel and cancel.is_set():
        raise DownloadCancelled("Download cancelled")
    if total is not None and downloaded != total:
        raise RuntimeError(f"Incomplete download: expected {total} bytes, got {downloaded}")
    part.replace(destination)


def download_detected_video(
    pid: str,
    output_dir: Path,
    progress: Callable[[int, int | None, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    if not pid.isdigit():
        raise ValueError("Invalid video ID")
    if cancel and cancel.is_set():
        raise DownloadCancelled("Download cancelled")
    history_db = find_history_db(None)
    webapp_urls = [url for url, _ in recent_webapp_urls(history_db)]
    feed, init_data = request_feed(pid, webapp_urls)
    item = next(
        (entry for entry in feed.get("items", []) if str(entry.get("pid")) == pid),
        None,
    )
    if not item or not item.get("video"):
        raise RuntimeError(f"No downloadable video returned for video ID {pid}")
    video_url = supported_video_url(str(item["video"]))
    referer = APP_BASE + "?" + urllib.parse.urlencode({"pid": pid})
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / safe_filename(str(item.get("title", "video")), pid)
    if cancel and cancel.is_set():
        raise DownloadCancelled("Download cancelled")
    if destination.is_file() and destination.stat().st_size:
        return destination
    download_video(video_url, destination, referer, init_data, progress, cancel)
    return destination


def telegram_is_running() -> bool:
    if os.name != "nt":
        return False
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Telegram.exe", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=creation_flags,
    )
    return "telegram.exe" in result.stdout.lower()


def debugging_browser(port: int) -> dict:
    endpoint = f"http://127.0.0.1:{port}/json/version"
    with urllib.request.urlopen(endpoint, timeout=1) as response:
        browser = json.load(response)
    if not isinstance(browser, dict) or not browser.get("webSocketDebuggerUrl"):
        raise RuntimeError("The debugging endpoint has no browser WebSocket URL")
    return browser


def wait_for_debugging_endpoint(port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            debugging_browser(port)
            return
        except (OSError, ValueError, urllib.error.URLError):
            time.sleep(1)
    raise RuntimeError(
        f"WebView2 debugging endpoint did not appear on 127.0.0.1:{port}; "
        "open a Mini App in Telegram and retry"
    )


def ensure_debugging_endpoint(
    port: int, launch_telegram: bool, on_status: Callable[[str], None] | None = None
) -> None:
    try:
        debugging_browser(port)
        return
    except (OSError, ValueError, urllib.error.URLError):
        pass

    if not launch_telegram:
        raise RuntimeError(f"No WebView2 debugging endpoint on 127.0.0.1:{port}")
    if telegram_is_running():
        raise RuntimeError(
            "Telegram is already running without WebView debugging. Exit Telegram "
            "completely (including the tray icon), then run --monitor again."
        )

    executable = telegram_executable()
    if not executable or not executable.is_file():
        raise RuntimeError("Telegram.exe was not found")

    environment = os.environ.copy()
    extra = environment.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    debug_args = (
        f"--remote-debugging-port={port} "
        "--remote-debugging-address=127.0.0.1"
    )
    environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"{extra} {debug_args}".strip()
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(executable), "-noupdate"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    message = "Telegram started. Open a Mini App so its WebView2 process is created."
    if on_status:
        on_status(message)
    else:
        print(message)
    wait_for_debugging_endpoint(port, 120)


def sanitized_page_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
    )


def is_video_response(url: str, mime_type: str, resource_type: str) -> bool:
    mime = mime_type.lower().split(";", 1)[0].strip()
    if mime.startswith("video/") or mime in VIDEO_MIME_TYPES:
        return True
    if resource_type.lower() == "media":
        return True
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    return suffix in VIDEO_EXTENSIONS


def video_pid(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    app = urllib.parse.urlsplit(APP_BASE)
    prefix = app.path.rstrip("/") + "/v/"
    if parsed.scheme != app.scheme or parsed.netloc.lower() != app.netloc.lower():
        return None
    if not parsed.path.startswith(prefix):
        return None
    pid = parsed.path[len(prefix) :].split("/", 1)[0]
    return pid if pid.isdigit() else None


def lookup_video_title(video_url: str, webapp_url: str) -> str | None:
    pid = video_pid(video_url)
    if not pid:
        return None
    init_data = init_data_from_url(webapp_url)
    referer = APP_BASE + "?" + urllib.parse.urlencode({"pid": pid})
    feed_url = APP_BASE + "/api/feed?" + urllib.parse.urlencode(
        {"src": "one", "q": pid, "offset": 0, "n": 8}
    )
    feed = request_json(feed_url, init_data, referer)
    item = next(
        (entry for entry in feed.get("items", []) if str(entry.get("pid")) == pid),
        None,
    )
    title = str(item.get("title", "")).strip() if item else ""
    return title or None


def video_record(message: dict, target: dict) -> dict | None:
    if message.get("method") != "Network.responseReceived":
        return None
    params = message.get("params", {})
    response = params.get("response", {})
    url = str(response.get("url", ""))
    mime_type = str(response.get("mimeType", ""))
    resource_type = str(params.get("type", ""))
    if not url.startswith(("http://", "https://")):
        return None
    if not is_video_response(url, mime_type, resource_type):
        return None
    return {
        "detected_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "video_url": url,
        "mime_type": mime_type,
        "status": int(response.get("status", 0)),
        "from_disk_cache": bool(response.get("fromDiskCache", False)),
        "page_title": str(target.get("title", "")),
        "page_url": sanitized_page_url(str(target.get("url", ""))),
    }


def monitor_video_links(
    args: argparse.Namespace,
    on_video: Callable[[dict], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
) -> int:
    try:
        import websocket
    except ImportError as error:
        raise RuntimeError(
            "Monitor mode requires websocket-client: pip install websocket-client"
        ) from error

    ensure_debugging_endpoint(
        args.debug_port, not args.no_launch_telegram, on_status=on_status
    )
    if args.monitor_log is not None:
        args.monitor_log.parent.mkdir(parents=True, exist_ok=True)
    recently_seen: dict[str, float] = {}
    video_titles: dict[str, str] = {}
    session_targets: dict[str, dict] = {}
    enabled_sessions: set[str] = set()
    next_command_id = 1

    browser = debugging_browser(args.debug_port)
    connection = websocket.create_connection(
        browser["webSocketDebuggerUrl"],
        timeout=0.5,
        suppress_origin=True,
        http_proxy_host=None,
    )

    def send_command(
        method: str, params: dict | None = None, session_id: str | None = None
    ) -> int:
        nonlocal next_command_id
        command: dict = {"id": next_command_id, "method": method}
        if params is not None:
            command["params"] = params
        if session_id is not None:
            command["sessionId"] = session_id
        connection.send(json.dumps(command))
        next_command_id += 1
        return next_command_id - 1

    def enable_session(session_id: str, target: dict) -> None:
        session_targets[session_id] = target
        if session_id in enabled_sessions:
            return
        enabled_sessions.add(session_id)
        if str(target.get("type", "")) in {"page", "webview", "iframe"}:
            send_command("Network.enable", session_id=session_id)
            if on_status is None:
                print(f"Attached: {target.get('title') or target.get('url') or target.get('targetId')}")
        send_command("Runtime.runIfWaitingForDebugger", session_id=session_id)

    send_command("Target.setDiscoverTargets", {"discover": True})
    send_command(
        "Target.setAutoAttach",
        {
            "autoAttach": True,
            "waitForDebuggerOnStart": True,
            "flatten": True,
        },
    )

    if on_status:
        on_status("已连接 Telegram，等待视频播放")
    else:
        print(f"Monitoring Telegram WebView video requests on 127.0.0.1:{args.debug_port}")
        if args.monitor_log is not None:
            print(f"Log: {args.monitor_log}")
        print("Press Ctrl+C to stop.")
    try:
        while not (stop_event and stop_event.is_set()):
            try:
                raw = connection.recv()
            except websocket.WebSocketTimeoutException:
                raw = None
            if raw:
                message = json.loads(raw)
                method = message.get("method")
                params = message.get("params", {})
                if method == "Target.attachedToTarget":
                    enable_session(str(params["sessionId"]), params.get("targetInfo", {}))
                elif method == "Target.detachedFromTarget":
                    session_id = str(params.get("sessionId", ""))
                    session_targets.pop(session_id, None)
                    enabled_sessions.discard(session_id)
                elif method == "Target.targetInfoChanged":
                    changed = params.get("targetInfo", {})
                    target_id = changed.get("targetId")
                    for session_id, target in list(session_targets.items()):
                        if target.get("targetId") == target_id:
                            session_targets[session_id] = changed
                session_id = str(message.get("sessionId", ""))
                target = session_targets.get(session_id, {})
                record = video_record(message, target)
                if record:
                    now = time.monotonic()
                    video_url = record["video_url"]
                    previous = recently_seen.get(video_url, float("-inf"))
                    if now - previous >= args.dedupe_seconds:
                        recently_seen[video_url] = now
                        pid = video_pid(video_url)
                        title = video_titles.get(pid, "") if pid else ""
                        if pid and not title:
                            try:
                                title = lookup_video_title(
                                    video_url, str(target.get("url", ""))
                                ) or ""
                            except (OSError, ValueError, RuntimeError):
                                pass
                            if title:
                                video_titles[pid] = title
                        if title:
                            record["video_title"] = title
                        if args.monitor_log is not None:
                            line = json.dumps(record, ensure_ascii=False)
                            with args.monitor_log.open("a", encoding="utf-8") as log:
                                log.write(line + "\n")
                        if on_video:
                            on_video(record)
                        else:
                            print(f"\n[{record['detected_at']}] {record['mime_type']}")
                            print(f"Title: {title or '(unavailable)'}")
                            print(video_url)

            cutoff = time.monotonic() - max(args.dedupe_seconds, 1) * 10
            recently_seen = {
                url: seen_at
                for url, seen_at in recently_seen.items()
                if seen_at >= cutoff
            }
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        return 0
    except (OSError, ValueError, websocket.WebSocketException) as error:
        raise RuntimeError(f"WebView2 debugging connection closed: {error}") from error
    finally:
        connection.close()
    return 0


def main() -> int:
    args = parse_args()
    try:
        if args.monitor:
            return monitor_video_links(args)
        pid, deep_link = parse_link(args.link)
        history_db = find_history_db(args.history_db)
        if args.no_launch:
            recent = recent_webapp_urls(history_db)
            if not recent:
                raise RuntimeError("Open this Mini App in Telegram Desktop first")
            webapp_urls = [url for url, _ in recent]
        else:
            webapp_url = refresh_webapp_url(history_db, deep_link)
            webapp_urls = [webapp_url]
            webapp_urls.extend(
                url
                for url, _ in recent_webapp_urls(history_db)
                if url != webapp_url
            )

        referer = APP_BASE + "?" + urllib.parse.urlencode({"pid": pid})
        feed, init_data = request_feed(pid, webapp_urls)
        item = next((entry for entry in feed.get("items", []) if str(entry.get("pid")) == pid), None)
        if not item or not item.get("video"):
            raise RuntimeError(f"No downloadable video returned for startapp={pid}")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        destination = args.output_dir / safe_filename(str(item.get("title", "video")), pid)
        if destination.is_file() and destination.stat().st_size:
            print(f"Already downloaded: {destination}")
            return 0

        video_url = supported_video_url(str(item["video"]))
        print(f"Title: {item.get('title', '')}")
        print(f"Saving to: {destination}")
        download_video(video_url, destination, referer, init_data)
        print(f"Done: {destination} ({destination.stat().st_size} bytes)")
        return 0
    except urllib.error.HTTPError as error:
        print(f"Error: HTTP {error.code}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
