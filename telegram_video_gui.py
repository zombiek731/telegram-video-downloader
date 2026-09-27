"""Desktop companion for choosing Telegram Mini App videos to download."""

from __future__ import annotations

import sys
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QSettings, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from download_telegram_video import (
    DownloadCancelled,
    download_detected_video,
    monitor_video_links,
    video_pid,
)


class Events(QObject):
    video = Signal(object)
    monitor_status = Signal(str)
    monitor_stopped = Signal(str)
    download_progress = Signal(str, int, object, float)
    download_finished = Signal(str, str, str)


class VideoAssistant(QMainWindow):
    def __init__(self, start_monitor: bool = True, use_tray: bool = True) -> None:
        super().__init__()
        self.events = Events()
        self.events.video.connect(self.add_video)
        self.events.monitor_status.connect(self.status_label_set)
        self.events.monitor_stopped.connect(self.monitor_stopped)
        self.events.download_progress.connect(self.update_progress)
        self.events.download_finished.connect(self.finish_download)
        self.settings = QSettings("TelegramVideoAssistant", "TelegramVideoAssistant")
        default_output = (
            Path.home() / "Downloads" / "Telegram Videos"
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent / "downloads"
        )
        self.output_dir = Path(
            self.settings.value(
                "output_dir", str(default_output)
            )
        )
        self.monitor_stop = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.video_rows: dict[str, QTreeWidgetItem] = {}
        self.video_buttons: dict[str, QPushButton] = {}
        self.task_rows: dict[str, QTreeWidgetItem] = {}
        self.task_buttons: dict[str, QPushButton] = {}
        self.pending: list[str] = []
        self.current_pid: str | None = None
        self.cancel_events: dict[str, threading.Event] = {}
        self._quitting = False

        self.setWindowTitle("Telegram 视频下载助手")
        self.resize(490, 660)
        self.build_ui()
        self.tray: QSystemTrayIcon | None = None
        if use_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.build_tray()
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.top() + 48)
        if start_monitor:
            self.start_monitor()

    def build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(12)

        title = QLabel("视频下载助手")
        title.setStyleSheet("font-size: 20px; font-weight: 600")
        layout.addWidget(title)

        status_row = QHBoxLayout()
        self.status_label = QLabel("正在连接 Telegram…")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        self.retry_button = QPushButton("重试连接")
        self.retry_button.clicked.connect(self.start_monitor)
        self.retry_button.setEnabled(False)
        status_row.addWidget(self.retry_button)
        layout.addLayout(status_row)

        hint = QLabel("播放小程序视频后会出现在下方。列表也可能包含预加载内容，请按标题选择。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666")
        layout.addWidget(hint)

        layout.addWidget(QLabel("最近识别"))
        self.video_tree = QTreeWidget()
        self.video_tree.setHeaderLabels(["视频", "时间", "操作"])
        self.video_tree.setRootIsDecorated(False)
        self.video_tree.setColumnWidth(0, 285)
        self.video_tree.setColumnWidth(1, 65)
        self.video_tree.setColumnWidth(2, 80)
        layout.addWidget(self.video_tree, 3)

        layout.addWidget(QLabel("下载任务"))
        self.task_tree = QTreeWidget()
        self.task_tree.setHeaderLabels(["视频", "状态", "操作"])
        self.task_tree.setRootIsDecorated(False)
        self.task_tree.setColumnWidth(0, 250)
        self.task_tree.setColumnWidth(1, 100)
        self.task_tree.setColumnWidth(2, 80)
        layout.addWidget(self.task_tree, 2)

        controls = QHBoxLayout()
        self.folder_button = QPushButton("打开下载文件夹")
        self.folder_button.clicked.connect(self.open_folder)
        controls.addWidget(self.folder_button)
        choose_button = QPushButton("更改位置")
        choose_button.clicked.connect(self.choose_folder)
        controls.addWidget(choose_button)
        layout.addLayout(controls)
        self.folder_label = QLabel(str(self.output_dir))
        self.folder_label.setStyleSheet("color: #666")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        self.setCentralWidget(root)

    def build_tray(self) -> None:
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
        self.setWindowIcon(icon)
        tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.show_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        tray.show()
        self.tray = tray

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def status_label_set(self, message: str) -> None:
        if message.startswith("Telegram started"):
            message = "Telegram 已启动。请打开小程序，正在等待监听连接…"
        elif message.startswith("Telegram running. Open a Mini App"):
            message = "Telegram 正在运行。请打开小程序，正在等待自动连接…"
        self.status_label.setText(message)

    def start_monitor(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        self.monitor_stop = threading.Event()
        self.retry_button.setEnabled(False)
        self.status_label.setText("正在连接 Telegram…")
        self.monitor_thread = threading.Thread(target=self.run_monitor, daemon=True)
        self.monitor_thread.start()

    def run_monitor(self) -> None:
        args = SimpleNamespace(
            debug_port=9222,
            no_launch_telegram=False,
            monitor_log=None,
            dedupe_seconds=3.0,
        )
        while not self.monitor_stop.is_set():
            try:
                monitor_video_links(
                    args,
                    on_video=self.events.video.emit,
                    on_status=self.events.monitor_status.emit,
                    stop_event=self.monitor_stop,
                )
                if not self.monitor_stop.is_set():
                    self.events.monitor_stopped.emit("监听已停止")
                return
            except Exception as error:
                message = str(error)
                if "WebView2 debugging connection closed" in message:
                    self.events.monitor_status.emit(
                        "小程序连接已断开。请重新打开小程序，正在等待自动连接…"
                    )
                    if self.monitor_stop.wait(1):
                        return
                    continue
                if "WebView2 debugging endpoint did not appear" in message:
                    message = (
                        "未检测到 Telegram 小程序监听。请先打开小程序；若已打开仍无法连接，"
                        "请从系统托盘完全退出 Telegram，再点击重试连接。"
                    )
                elif "Telegram.exe was not found" in message:
                    message = "找不到 Telegram Desktop。请确认已经安装并登录。"
                self.events.monitor_stopped.emit(message)
                return

    def monitor_stopped(self, message: str) -> None:
        self.status_label.setText(message)
        self.retry_button.setEnabled(True)

    def add_video(self, record: dict) -> None:
        pid = video_pid(str(record.get("video_url", "")))
        if not pid or not str(record.get("mime_type", "")).lower().startswith("video/mp4"):
            return
        title = str(record.get("video_title") or f"视频 {pid}")
        detected = str(record.get("detected_at", ""))
        clock = detected[11:16] if len(detected) >= 16 else ""
        if pid in self.video_rows:
            old = self.video_rows[pid]
            if not record.get("video_title"):
                title = old.text(0)
            self.video_tree.takeTopLevelItem(self.video_tree.indexOfTopLevelItem(old))
        item = QTreeWidgetItem([title, clock, ""])
        self.video_rows[pid] = item
        button = QPushButton("下载")
        button.clicked.connect(lambda _checked=False, video_id=pid: self.enqueue(video_id))
        if pid == self.current_pid or pid in self.pending:
            button.setEnabled(False)
        elif pid in self.task_rows and self.task_rows[pid].text(1) == "完成":
            button.setText("已下载")
            button.setEnabled(False)
        self.video_buttons[pid] = button
        self.video_tree.insertTopLevelItem(0, item)
        self.video_tree.setItemWidget(item, 2, button)
        if self.video_tree.topLevelItemCount() > 50:
            old = self.video_tree.takeTopLevelItem(50)
            old_pid = next((key for key, value in self.video_rows.items() if value is old), None)
            if old_pid:
                self.video_rows.pop(old_pid)
                self.video_buttons.pop(old_pid)

    def enqueue(self, pid: str) -> None:
        if pid in self.pending or pid == self.current_pid:
            return
        self.pending.append(pid)
        title = self.video_rows[pid].text(0)
        if pid in self.task_rows:
            item = self.task_rows[pid]
            item.setText(0, title)
            item.setText(1, "排队中")
        else:
            item = QTreeWidgetItem([title, "排队中", ""])
            self.task_rows[pid] = item
            self.task_tree.insertTopLevelItem(0, item)
            button = QPushButton("取消")
            button.clicked.connect(lambda _checked=False, video_id=pid: self.cancel_download(video_id))
            self.task_buttons[pid] = button
            self.task_tree.setItemWidget(item, 2, button)
        self.task_buttons[pid].setEnabled(True)
        self.video_buttons[pid].setEnabled(False)
        self.start_next_download()

    def start_next_download(self) -> None:
        if self.current_pid or not self.pending:
            return
        pid = self.pending.pop(0)
        self.current_pid = pid
        self.task_rows[pid].setText(1, "连接中")
        cancel = threading.Event()
        self.cancel_events[pid] = cancel
        output_dir = self.output_dir

        def run() -> None:
            try:
                path = download_detected_video(
                    pid,
                    output_dir,
                    lambda done, total, speed: self.events.download_progress.emit(
                        pid, done, total, speed
                    ),
                    cancel,
                )
                self.events.download_finished.emit(pid, "完成", str(path))
            except DownloadCancelled:
                self.events.download_finished.emit(pid, "已取消", "")
            except urllib.error.HTTPError as error:
                self.events.download_finished.emit(pid, "失败", f"HTTP {error.code}")
            except Exception as error:
                self.events.download_finished.emit(pid, "失败", str(error))

        threading.Thread(target=run, daemon=True).start()

    def update_progress(self, pid: str, done: int, total: object, speed: float) -> None:
        if pid != self.current_pid:
            return
        if self.cancel_events.get(pid) and self.cancel_events[pid].is_set():
            return
        if isinstance(total, int) and total > 0:
            status = f"{done * 100 / total:.0f}% · {speed:.1f} MiB/s"
        else:
            status = f"{done / 1024 / 1024:.1f} MiB · {speed:.1f} MiB/s"
        self.task_rows[pid].setText(1, status)

    def finish_download(self, pid: str, state: str, detail: str) -> None:
        self.task_rows[pid].setText(1, state)
        self.task_rows[pid].setToolTip(1, detail)
        if state == "失败":
            if "WebView History was not found" in detail:
                detail = "请先在 Telegram 中打开小程序，再重试下载。"
            elif "authentication data was rejected" in detail:
                detail = "登录信息已过期，请重新打开小程序，再重试下载。"
            elif detail in {"HTTP 403", "HTTP 404"}:
                detail = "视频地址不可用，请重新播放该视频，再重试下载。"
            self.status_label.setText(f"下载失败：{detail}")
        self.task_buttons[pid].setEnabled(False)
        if pid in self.video_buttons:
            self.video_buttons[pid].setEnabled(state != "完成")
            self.video_buttons[pid].setText("已下载" if state == "完成" else "下载")
        self.cancel_events.pop(pid, None)
        self.current_pid = None
        self.start_next_download()

    def cancel_download(self, pid: str) -> None:
        if pid in self.pending:
            self.pending.remove(pid)
            self.task_rows[pid].setText(1, "已取消")
            self.task_buttons[pid].setEnabled(False)
            if pid in self.video_buttons:
                self.video_buttons[pid].setEnabled(True)
        elif pid in self.cancel_events:
            self.cancel_events[pid].set()
            self.task_rows[pid].setText(1, "取消中")
            self.task_buttons[pid].setEnabled(False)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择下载位置", str(self.output_dir))
        if folder:
            self.output_dir = Path(folder)
            self.settings.setValue("output_dir", folder)
            self.folder_label.setText(folder)

    def open_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def quit_app(self) -> None:
        self._quitting = True
        self.monitor_stop.set()
        for event in self.cancel_events.values():
            event.set()
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray and not self._quitting:
            self.hide()
            event.ignore()
        else:
            self.monitor_stop.set()
            for cancel in self.cancel_events.values():
                cancel.set()
            event.accept()


def main() -> int:
    self_test = "--self-test" in sys.argv
    app = QApplication([sys.argv[0]] if self_test else sys.argv)
    if self_test:
        import websocket  # noqa: F401

        window = VideoAssistant(start_monitor=False, use_tray=False)
        window.add_video(
            {
                "video_url": "https://cdn.asset-7k2m.com/app/v/1/test",
                "mime_type": "video/mp4",
                "video_title": "Test",
            }
        )
        window.show()
        app.processEvents()
        if not window.isVisible() or window.video_tree.topLevelItemCount() != 1:
            return 1
        window.close()
        return 0
    app.setQuitOnLastWindowClosed(not QSystemTrayIcon.isSystemTrayAvailable())
    window = VideoAssistant()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
