import unittest
import tempfile
import threading
import sys
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from download_telegram_video import (
    DownloadCancelled,
    TelegramAuthenticationError,
    download_detected_video,
    download_video,
    is_video_response,
    lookup_video_title,
    monitor_video_links,
    parse_args,
    parse_link,
    request_feed,
    sanitized_page_url,
    supported_video_url,
    video_pid,
    video_record,
)


class MonitorHelpersTest(unittest.TestCase):
    def test_monitor_does_not_save_signed_urls_by_default(self):
        with patch.object(sys, "argv", ["download_telegram_video.py", "--monitor"]):
            self.assertIsNone(parse_args().monitor_log)

    def test_normalizes_legacy_bot_link(self):
        pid, deep_link = parse_link(
            "https://t.me/ai_ym_lyf_bot/ai_tiktoporn?startapp=1461"
        )
        self.assertEqual(pid, "1461")
        self.assertEqual(
            deep_link,
            "tg://resolve?domain=lyf_ym_bot&appname=ai_tiktoporn&startapp=1461",
        )

    @patch("download_telegram_video.request_json")
    def test_tries_older_authentication_data_after_rejection(self, request_json):
        request_json.side_effect = [
            TelegramAuthenticationError("rejected"),
            {"items": [{"pid": 1461}]},
        ]

        feed, init_data = request_feed(
            "1461",
            [
                "https://cdn.asset-7k2m.com/app#tgWebAppData=rejected",
                "https://cdn.asset-7k2m.com/app#tgWebAppData=accepted",
            ],
        )

        self.assertEqual(feed, {"items": [{"pid": 1461}]})
        self.assertEqual(init_data, "accepted")
        self.assertEqual(request_json.call_count, 2)

    def test_detects_extensionless_mp4_response(self):
        message = {
            "method": "Network.responseReceived",
            "params": {
                "type": "Media",
                "response": {
                    "url": "https://example.test/media/signed-token",
                    "mimeType": "video/mp4",
                    "status": 206,
                },
            },
        }
        record = video_record(
            message,
            {"title": "Mini App", "url": "https://example.test/app#secret"},
        )
        self.assertEqual(record["video_url"], "https://example.test/media/signed-token")
        self.assertEqual(record["page_url"], "https://example.test/app")

    def test_ignores_non_video_response(self):
        self.assertFalse(
            is_video_response("https://example.test/app.js", "text/javascript", "Script")
        )

    def test_detects_stream_manifests(self):
        self.assertTrue(
            is_video_response(
                "https://example.test/master", "application/vnd.apple.mpegurl", "Fetch"
            )
        )

    def test_removes_fragment_from_page_url(self):
        self.assertEqual(
            sanitized_page_url("https://example.test/app?pid=640#tgWebAppData=secret"),
            "https://example.test/app?pid=640",
        )

    def test_extracts_video_pid_from_supported_cdn_url(self):
        self.assertEqual(
            video_pid("https://cdn.asset-7k2m.com/app/v/965/user/token/signature"),
            "965",
        )
        self.assertIsNone(video_pid("https://example.test/app/v/965/user/token"))

    @patch("download_telegram_video.request_json")
    def test_looks_up_video_title_by_pid(self, request_json):
        request_json.return_value = {
            "items": [
                {"pid": 964, "title": "Other"},
                {"pid": 965, "title": "Expected title"},
            ]
        }
        title = lookup_video_title(
            "https://cdn.asset-7k2m.com/app/v/965/user/token/signature",
            "https://cdn.asset-7k2m.com/app?pid=965#tgWebAppData=auth-token",
        )
        self.assertEqual(title, "Expected title")

    @patch("download_telegram_video.download_video")
    @patch("download_telegram_video.request_feed")
    @patch("download_telegram_video.recent_webapp_urls")
    @patch("download_telegram_video.find_history_db")
    def test_selected_video_uses_fresh_feed_address(
        self, find_db, recent_urls, request_feed, download
    ):
        find_db.return_value = Path("History")
        recent_urls.return_value = [("webapp-auth", 1)]
        request_feed.return_value = (
            {"items": [{"pid": 640, "title": "Chosen", "video": "/app/v/640/new-token"}]},
            "fresh-auth",
        )
        with tempfile.TemporaryDirectory() as folder:
            path = download_detected_video("640", Path(folder))
            self.assertEqual(path.name, "Chosen [640].mp4")
        request_feed.assert_called_once_with("640", ["webapp-auth"])
        self.assertEqual(download.call_args.args[0], "https://cdn.asset-7k2m.com/app/v/640/new-token")
        self.assertEqual(download.call_args.args[3], "fresh-auth")

    @patch("download_telegram_video.download_video")
    @patch("download_telegram_video.request_feed")
    @patch("download_telegram_video.recent_webapp_urls", return_value=[("webapp-auth", 1)])
    @patch("download_telegram_video.find_history_db", return_value=Path("History"))
    def test_rejects_external_video_host_before_sending_authentication(
        self, _find_db, _recent_urls, request_feed, download
    ):
        self.assertEqual(
            supported_video_url("/app/v/640/token"),
            "https://cdn.asset-7k2m.com/app/v/640/token",
        )
        request_feed.return_value = (
            {"items": [{"pid": 640, "title": "Bad", "video": "https://other.example/video.mp4"}]},
            "fresh-auth",
        )
        with self.assertRaises(ValueError):
            download_detected_video("640", Path("unused"))
        download.assert_not_called()

    def test_cancelled_download_keeps_partial_file_for_resume(self):
        payload = b"a" * (2 * 1024 * 1024 + 17)
        ranges = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                header = self.headers.get("Range")
                ranges.append(header)
                start = int(header.split("=")[1].split("-")[0]) if header else 0
                body = payload[start:]
                self.send_response(206 if header else 200)
                self.send_header("Content-Length", str(len(body)))
                if header:
                    self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "video.mp4"
                cancel = threading.Event()
                url = f"http://127.0.0.1:{server.server_port}/video.mp4"

                def stop_after_chunk(_done, _total, _speed):
                    cancel.set()

                with self.assertRaises(DownloadCancelled):
                    download_video(url, target, "referer", "auth", stop_after_chunk, cancel)
                self.assertEqual(target.with_suffix(".mp4.part").stat().st_size, 1024 * 1024)
                download_video(url, target, "referer", "auth", lambda *_: None)
                self.assertEqual(target.read_bytes(), payload)
                self.assertEqual(ranges, [None, "bytes=1048576-"])
        finally:
            server.shutdown()
            server.server_close()

    @patch("download_telegram_video.lookup_video_title", return_value="Selected title")
    @patch("download_telegram_video.debugging_browser")
    @patch("download_telegram_video.ensure_debugging_endpoint")
    @patch("websocket.create_connection")
    def test_monitor_delivers_video_to_gui_without_writing_url_log(
        self, create_connection, _ensure, debugging_browser, _lookup
    ):
        debugging_browser.return_value = {"webSocketDebuggerUrl": "ws://localhost/devtools"}
        messages = [
            '{"method":"Target.attachedToTarget","params":{"sessionId":"s1","targetInfo":{"type":"page","url":"https://cdn.asset-7k2m.com/app#tgWebAppData=auth"}}}',
            '{"method":"Network.responseReceived","sessionId":"s1","params":{"type":"Media","response":{"url":"https://cdn.asset-7k2m.com/app/v/640/token","mimeType":"video/mp4","status":206}}}',
        ]
        create_connection.return_value.recv.side_effect = messages
        stop = threading.Event()
        received = []

        def on_video(record):
            received.append(record)
            stop.set()

        result = monitor_video_links(
            SimpleNamespace(debug_port=9222, no_launch_telegram=False, monitor_log=None, dedupe_seconds=3),
            on_video=on_video,
            on_status=lambda _message: None,
            stop_event=stop,
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["video_title"], "Selected title")
        create_connection.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
