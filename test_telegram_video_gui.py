import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from telegram_video_gui import VideoAssistant


def record(pid, title):
    return {
        "video_url": f"https://cdn.asset-7k2m.com/app/v/{pid}/new-token",
        "mime_type": "video/mp4",
        "video_title": title,
        "detected_at": "2026-09-25T10:30:00+08:00",
    }


class VideoAssistantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = VideoAssistant(start_monitor=False, use_tray=False)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    @patch("telegram_video_gui.monitor_video_links")
    def test_reconnects_after_webview_disconnects(self, monitor):
        def run(_args, **_kwargs):
            if monitor.call_count == 1:
                raise RuntimeError("WebView2 debugging connection closed: disconnected")
            self.window.monitor_stop.set()
            return 0

        monitor.side_effect = run
        with patch.object(self.window.monitor_stop, "wait", return_value=False):
            self.window.run_monitor()

        self.assertEqual(monitor.call_count, 2)
        self.assertIn("重新打开小程序", self.window.status_label.text())

    @patch("telegram_video_gui.monitor_video_links")
    def test_timeout_explains_how_to_reconnect(self, monitor):
        monitor.side_effect = RuntimeError("WebView2 debugging endpoint did not appear")

        self.window.run_monitor()

        self.assertIn("打开小程序", self.window.status_label.text())
        self.assertIn("完全退出 Telegram", self.window.status_label.text())
        self.assertTrue(self.window.retry_button.isEnabled())

    def test_deduplicates_by_video_id_and_ignores_unsupported_sources(self):
        self.window.add_video(record("100", "First"))
        self.window.add_video(record("101", "Second"))
        self.window.add_video(record("100", "Updated"))
        self.assertEqual(self.window.video_tree.topLevelItemCount(), 2)
        self.assertEqual(self.window.video_tree.topLevelItem(0).text(0), "Updated")
        self.window.add_video({**record("102", "Stream"), "mime_type": "application/vnd.apple.mpegurl"})
        self.window.add_video({**record("102", "Other site"), "video_url": "https://other.test/v/102"})
        self.assertEqual(self.window.video_tree.topLevelItemCount(), 2)

    @patch("telegram_video_gui.download_detected_video")
    def test_queue_keeps_selected_ids_while_new_videos_arrive(self, download):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def run(pid, _folder, _progress, _cancel):
            calls.append(pid)
            if pid == "100":
                entered.set()
                release.wait(3)
            return Path(f"{pid}.mp4")

        download.side_effect = run
        self.window.add_video(record("100", "First"))
        self.window.enqueue("100")
        self.assertTrue(entered.wait(2))
        self.window.add_video(record("101", "Second"))
        self.window.enqueue("101")
        self.window.add_video(record("102", "Third"))
        self.assertEqual(self.window.pending, ["101"])
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.window.current_pid is not None:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertEqual(calls, ["100", "101"])
        self.assertEqual(self.window.task_rows["100"].text(1), "完成")
        self.assertEqual(self.window.task_rows["101"].text(1), "完成")


if __name__ == "__main__":
    unittest.main()
