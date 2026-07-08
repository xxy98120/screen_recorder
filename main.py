"""屏幕录制软件的UI模块"""

import sys
from pathlib import Path

import cv2
import mss
import numpy as np


def _resource_path(relative_path: str) -> Path:
    """获取资源文件路径（兼容开发环境和打包后的 exe）"""
    base = getattr(sys, "_MEIPASS", Path(__file__).parent)
    return Path(base) / relative_path

from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
    QBrush,
    QPen,
    QCloseEvent,
    QImage,
)
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QCheckBox,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from recording_engine import RecordingEngine
from window_capture import capture_window_content
from window_utils import get_visible_windows, is_window_minimized


# ── 颜色主题 ──────────────────────────────────────────────
C_PRIMARY = QColor("#6366f1")      # 靛蓝
C_PRIMARY_DARK = QColor("#4f46e5")
C_BG = QColor("#1e1e2e")
C_BG2 = QColor("#2a2a3e")
C_BG3 = QColor("#363650")
C_TEXT = QColor("#e0e0f0")
C_TEXT2 = QColor("#a0a0c0")
C_RECORDING = QColor("#ef4444")
C_PAUSED = QColor("#f59e0b")
C_SUCCESS = QColor("#22c55e")

STYLE_BTN = f"""
QPushButton {{
    background-color: {C_PRIMARY.name()};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-height: 36px;
}}
QPushButton:hover {{
    background-color: {C_PRIMARY_DARK.name()};
}}
QPushButton:pressed {{
    background-color: {C_PRIMARY_DARK.name()};
}}
QPushButton:disabled {{
    background-color: #555;
    color: #888;
}}
"""


class RecordingIndicator(QWidget):
    """录制状态指示器（圆形指示灯 + 时长显示）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_recording = False
        self._is_paused = False
        self.setFixedSize(160, 40)

    def set_recording(self, recording: bool):
        self._is_recording = recording
        if not recording:
            self._is_paused = False
        self.update()

    def set_paused(self, paused: bool):
        self._is_paused = paused
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 指示灯
        radius = 8
        cx, cy = 20, self.height() // 2

        if self._is_recording:
            color = C_PAUSED if self._is_paused else C_RECORDING
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color.darker(130), 2))
            painter.drawEllipse(QPoint(cx, cy), radius, radius)

            # 闪烁效果（录制中）
            if not self._is_paused:
                painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 60)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPoint(cx, cy), radius + 6, radius + 6)
        else:
            painter.setBrush(QBrush(C_TEXT2))
            painter.setPen(QPen(C_TEXT2, 2))
            painter.drawEllipse(QPoint(cx, cy), radius, radius)

        painter.setPen(QPen(C_TEXT))
        painter.end()


class RecorderWindow(QMainWindow):
    """主窗口"""

    # 线程安全信号
    window_lost_signal = pyqtSignal()
    engine_status_signal = pyqtSignal(str)
    engine_frame_signal = pyqtSignal(float, int)
    engine_error_signal = pyqtSignal(str)
    recording_done_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._engine = RecordingEngine()
        self._setup_ui()
        self._connect_signals()
        self._init_tray()
        self._start_time = 0.0
        self._preview_sct = mss.mss()

        # 线程安全：将信号连接到主线程的处理函数
        self.window_lost_signal.connect(self._on_window_lost_ui)
        self.engine_status_signal.connect(self._on_engine_status)
        self.engine_frame_signal.connect(self._on_engine_frame)
        self.engine_error_signal.connect(self._on_engine_error)
        self.recording_done_signal.connect(self._on_recording_done)

        # 定时器 - 更新UI
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._update_ui)
        self._ui_timer.start(100)

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._update_preview)
        self._preview_timer.start(500)

    # ── UI 构建 ──────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("小开心录屏")
        # 设置窗口图标
        icon_path = _resource_path("icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(980, 560)
        self.resize(1080, 640)
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {C_BG.name()}; }}
            QLabel {{ color: {C_TEXT.name()}; font-size: 13px; }}
            QRadioButton {{
                color: {C_TEXT.name()};
                font-size: 13px;
                spacing: 6px;
            }}
            QRadioButton::indicator {{
                width: 16px; height: 16px;
                border: 2px solid {C_BG3.name()};
                border-radius: 8px;
                background-color: {C_BG2.name()};
            }}
            QRadioButton::indicator:checked {{
                border-color: {C_PRIMARY.name()};
                background-color: {C_PRIMARY.name()};
            }}
            QGroupBox {{
                color: {C_TEXT.name()};
                font-size: 14px;
                font-weight: bold;
                border: 1px solid {C_BG3.name()};
                border-radius: 6px;
                margin-top: 10px;
                padding: 10px 8px 8px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
            QSlider::groove:horizontal {{
                height: 6px;
                background: {C_BG3.name()};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {C_PRIMARY.name()};
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }}
            QSlider::sub-page:horizontal {{
                background: {C_PRIMARY.name()};
                border-radius: 3px;
            }}
            QComboBox {{
                background-color: {C_BG2.name()};
                color: {C_TEXT.name()};
                border: 1px solid {C_BG3.name()};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }}
            QComboBox:hover {{ border-color: {C_PRIMARY.name()}; }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {C_BG2.name()};
                color: {C_TEXT.name()};
                selection-background-color: {C_PRIMARY.name()};
                border: 1px solid {C_BG3.name()};
            }}
            QSpinBox {{
                background-color: {C_BG2.name()};
                color: {C_TEXT.name()};
                border: 1px solid {C_BG3.name()};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QSpinBox:focus {{ border-color: {C_PRIMARY.name()}; }}
            QCheckBox {{
                color: {C_TEXT.name()};
                font-size: 13px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid {C_BG3.name()};
                background-color: {C_BG2.name()};
            }}
            QCheckBox::indicator:checked {{
                background-color: {C_PRIMARY.name()};
                border-color: {C_PRIMARY.name()};
            }}
        """)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setSpacing(8)
        root_layout.setContentsMargins(12, 8, 12, 8)

        # ── 标题 ──
        title = QLabel("🎬 小开心录屏")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {C_TEXT.name()};")
        title.setAlignment(Qt.AlignCenter)
        title.setFixedHeight(26)
        root_layout.addWidget(title)

        author = QLabel("作者：小开心    QQ：9165599819")
        author.setStyleSheet(f"font-size: 12px; color: {C_TEXT2.name()};")
        author.setAlignment(Qt.AlignCenter)
        author.setFixedHeight(18)
        root_layout.addWidget(author)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)
        root_layout.addLayout(body_layout, 1)

        controls_panel = QWidget()
        controls_panel.setMinimumWidth(460)
        controls_panel.setMaximumWidth(560)
        layout = QVBoxLayout(controls_panel)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(controls_panel, 0)

        # ── 录制模式 ──
        mode_group = QGroupBox("录制模式")
        mode_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(4)
        mode_layout.setContentsMargins(8, 14, 8, 6)

        mode_radio_row = QHBoxLayout()
        self._mode_group = QButtonGroup(self)
        self._mode_screen_radio = QRadioButton("全屏录制")
        self._mode_screen_radio.setChecked(True)
        self._mode_window_radio = QRadioButton("窗口录制")
        self._mode_group.addButton(self._mode_screen_radio, 0)
        self._mode_group.addButton(self._mode_window_radio, 1)
        mode_radio_row.addWidget(self._mode_screen_radio)
        mode_radio_row.addWidget(self._mode_window_radio)
        mode_radio_row.addStretch()
        mode_layout.addLayout(mode_radio_row)

        # 窗口列表（默认隐藏）
        window_row = QHBoxLayout()
        self._window_combo = QComboBox()
        self._window_combo.setMinimumWidth(360)
        self._window_combo.setMaxVisibleItems(12)
        self._window_refresh_btn = QPushButton("刷新")
        self._window_refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C_BG3.name()}; color: {C_TEXT2.name()};
                border: 1px solid #555; border-radius: 6px;
                padding: 8px 16px; font-size: 13px; font-weight: bold;
                min-width: 60px;
            }}
            QPushButton:hover {{
                background-color: {C_BG2.name()};
                border-color: {C_PRIMARY.name()};
            }}
        """)
        window_row.addWidget(QLabel("目标窗口:"))
        window_row.addWidget(self._window_combo, 1)
        window_row.addWidget(self._window_refresh_btn)
        self._window_row_widget = QWidget()
        self._window_row_widget.setLayout(window_row)
        self._window_row_widget.setVisible(False)  # 默认隐藏
        mode_layout.addWidget(self._window_row_widget)

        layout.addWidget(mode_group)

        # ── 录制设置 ──
        settings_group = QGroupBox("录制设置")
        settings_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        settings_layout = QFormLayout(settings_group)
        settings_layout.setSpacing(4)
        settings_layout.setContentsMargins(8, 14, 8, 6)

        # 显示器选择
        self._monitor_combo = QComboBox()
        self._monitor_combo.addItem("主显示器")
        self._monitor_combo.addItem("显示器 2")
        settings_layout.addRow("显示器:", self._monitor_combo)

        # FPS
        fps_row = QHBoxLayout()
        self._fps_slider = QSlider(Qt.Horizontal)
        self._fps_slider.setRange(10, 60)
        self._fps_slider.setValue(30)
        self._fps_slider.setTickPosition(QSlider.TicksBelow)
        self._fps_slider.setTickInterval(10)
        self._fps_label = QLabel("30 FPS")
        self._fps_label.setStyleSheet(f"color: {C_PRIMARY.name()}; font-weight: bold;")
        fps_row.addWidget(self._fps_slider, 1)
        fps_row.addWidget(self._fps_label)
        settings_layout.addRow("帧率:", fps_row)

        # 音频
        self._audio_combo = QComboBox()
        self._audio_combo.addItem("不录制音频", "none")
        self._audio_combo.addItem("仅系统声音（游戏等）", "system")
        self._audio_combo.addItem("系统声音 + 麦克风", "both")
        self._audio_combo.setCurrentIndex(0)
        settings_layout.addRow("音频:", self._audio_combo)

        # 显示鼠标
        self._mouse_check = QCheckBox("显示鼠标指针")
        self._mouse_check.setChecked(True)
        settings_layout.addRow("", self._mouse_check)

        layout.addWidget(settings_group)

        # ── 状态 / 控制（占剩余空间）──
        status_group = QGroupBox("录制控制")
        status_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(6)
        status_layout.setContentsMargins(8, 14, 8, 6)

        # 状态指示
        self._indicator = RecordingIndicator(self)
        status_layout.addWidget(self._indicator, alignment=Qt.AlignCenter)

        self._status_label = QLabel("就绪 — 点击下方按钮开始录制")
        self._status_label.setStyleSheet(f"color: {C_TEXT2.name()}; font-size: 13px;")
        self._status_label.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self._status_label)

        self._duration_label = QLabel("00:00")
        self._duration_label.setStyleSheet(f"color: {C_TEXT.name()}; font-size: 28px; font-weight: bold;")
        self._duration_label.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self._duration_label)

        # 弹性空间，把按钮和路径推到最底部
        status_layout.addStretch(1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._record_btn = QPushButton("▶ 开始录制")
        self._record_btn.setStyleSheet(STYLE_BTN)
        btn_row.addWidget(self._record_btn)

        self._pause_btn = QPushButton("⏸ 暂停")
        self._pause_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C_BG3.name()}; color: {C_TEXT.name()};
                border: 1px solid #555; border-radius: 6px;
                padding: 8px 20px; font-size: 14px; font-weight: bold;
                min-height: 36px;
            }}
            QPushButton:hover {{
                background-color: #444; border-color: {C_PAUSED.name()};
            }}
            QPushButton:disabled {{ color: #555; }}
        """)
        self._pause_btn.setEnabled(False)
        btn_row.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("⏹ 停止")
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C_RECORDING.name()}; color: white;
                border: none; border-radius: 6px;
                padding: 8px 20px; font-size: 14px; font-weight: bold;
                min-height: 36px;
            }}
            QPushButton:hover {{
                background-color: #dc2626;
            }}
            QPushButton:disabled {{ background-color: #555; color: #888; }}
        """)
        self._stop_btn.setEnabled(False)
        btn_row.addWidget(self._stop_btn)

        status_layout.addLayout(btn_row)

        # 输出路径
        path_row = QHBoxLayout()
        self._path_label = QLabel(f"输出目录: {Path.home() / 'Videos'}")
        self._path_label.setStyleSheet(f"color: {C_TEXT2.name()}; font-size: 12px;")
        path_row.addWidget(self._path_label, 1)
        self._path_btn = QPushButton("选择...")
        self._path_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C_BG3.name()}; color: {C_TEXT2.name()};
                border: 1px solid #555; border-radius: 4px;
                padding: 4px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {C_PRIMARY.name()}; }}
        """)
        path_row.addWidget(self._path_btn)
        status_layout.addLayout(path_row)

        layout.addWidget(status_group)
        # ⚠️ 注意：不要加 addStretch()，录制控制组会自然占用剩余空间
        self._output_dir = Path.home() / "Videos"

        preview_group = QGroupBox("画面预览")
        preview_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setSpacing(8)
        preview_layout.setContentsMargins(8, 14, 8, 8)

        self._preview_label = QLabel("选择窗口后显示预览")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumSize(420, 260)
        self._preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._preview_label.setStyleSheet(f"""
            QLabel {{
                background-color: #08080c;
                color: {C_TEXT2.name()};
                border: 1px solid {C_BG3.name()};
                border-radius: 6px;
                font-size: 13px;
            }}
        """)
        preview_layout.addWidget(self._preview_label, 1)

        self._preview_info_label = QLabel("预览每 0.5 秒刷新一次")
        self._preview_info_label.setStyleSheet(f"color: {C_TEXT2.name()}; font-size: 12px;")
        preview_layout.addWidget(self._preview_info_label)
        body_layout.addWidget(preview_group, 1)

    def _init_tray(self):
        """初始化系统托盘"""
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("小开心录屏")

        # 使用 icon.ico（与窗口图标一致）
        icon_path = _resource_path("icon.ico")
        if icon_path.exists():
            self._tray.setIcon(QIcon(str(icon_path)))

        tray_menu = QMenu()
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.showNormal)
        tray_menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    # ── 信号连接 ──────────────────────────────────────────

    def _connect_signals(self):
        # 按钮事件
        self._record_btn.clicked.connect(self._on_record)
        self._pause_btn.clicked.connect(self._on_pause)
        self._stop_btn.clicked.connect(self._on_stop)
        self._path_btn.clicked.connect(self._on_select_path)

        # FPS滑块
        self._fps_slider.valueChanged.connect(self._on_fps_changed)

        # 录制引擎回调
        self._engine.on_status_changed = self.engine_status_signal.emit
        self._engine.on_frame_updated = self.engine_frame_signal.emit
        self._engine.on_error = self.engine_error_signal.emit
        # 窗口丢失回调（线程安全：仅发射信号，不操作UI）
        self._engine.on_window_lost = self.window_lost_signal.emit
        # 录制完成回调
        self._engine.on_recording_done = self.recording_done_signal.emit

        # 显示器切换
        self._monitor_combo.currentIndexChanged.connect(
            lambda idx: self._engine.set_monitor(idx + 1)
        )
        self._monitor_combo.currentIndexChanged.connect(lambda _idx: self._update_preview())

        # 模式切换（直接使用 clicked 信号，最可靠）
        self._mode_screen_radio.clicked.connect(lambda: self._on_mode_changed(0))
        self._mode_window_radio.clicked.connect(lambda: self._on_mode_changed(1))

        # 窗口刷新
        self._window_refresh_btn.clicked.connect(self._refresh_window_list)
        self._window_combo.currentIndexChanged.connect(lambda _idx: self._update_preview())

        # 初始刷新窗口列表
        self._refresh_window_list()
        self._update_preview()

    # ── 事件处理 ──────────────────────────────────────────

    def _on_mode_changed(self, btn_id: int):
        """录制模式切换"""
        window_mode = (btn_id == 1)  # 1=窗口录制, 0=全屏录制
        self._window_row_widget.setVisible(window_mode)
        self._monitor_combo.setEnabled(not window_mode)

        if window_mode:
            # 刷新窗口列表
            self._refresh_window_list()
            # 选中第一个有效项
            if self._window_combo.count() > 0 and self._window_combo.currentData() is None:
                self._window_combo.setCurrentIndex(0)
        else:
            # 全屏模式：使用当前选中的显示器
            idx = self._monitor_combo.currentIndex()
            self._engine.set_monitor(idx + 1)
        self._update_preview()

    def _refresh_window_list(self):
        """刷新窗口列表"""
        current_hwnd = self._window_combo.currentData()
        self._window_combo.clear()
        self._window_combo.addItem("-- 请选择窗口 --", None)

        windows = get_visible_windows()
        for win in windows:
            self._window_combo.addItem(win.display_name, win.hwnd)

        # 恢复上次选中的窗口
        if current_hwnd is not None:
            idx = self._window_combo.findData(current_hwnd)
            if idx >= 0:
                self._window_combo.setCurrentIndex(idx)

        count = len(windows)
        self._status_label.setText(f"找到 {count} 个可用窗口，建议根据进程名或 HWND 区分")
        self._update_preview()

    def _on_record(self):
        self._engine.fps = self._fps_slider.value()
        self._engine.show_mouse = self._mouse_check.isChecked()
        self._engine.audio_mode = self._audio_combo.currentData()

        if self._mode_window_radio.isChecked():
            hwnd = self._window_combo.currentData()
            if hwnd is None:
                QMessageBox.warning(self, "请选择窗口", "窗口录制模式需要先选择一个目标窗口。")
                return
            self._engine.set_window(hwnd)
            self._status_label.setText("窗口录制已就绪")

        self._engine.start_recording(self._output_dir)
        self._start_time = 0.0

        self._record_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("⏸ 暂停")
        self._stop_btn.setEnabled(True)
        self._indicator.set_recording(True)
        self._indicator.set_paused(False)

        # 录制期间禁用模式/来源切换
        self._mode_screen_radio.setEnabled(False)
        self._mode_window_radio.setEnabled(False)
        self._monitor_combo.setEnabled(False)
        self._window_combo.setEnabled(False)
        self._window_refresh_btn.setEnabled(False)

    def _on_pause(self):
        if self._engine.is_paused:
            self._engine.resume_recording()
            self._pause_btn.setText("⏸ 暂停")
            self._indicator.set_paused(False)
        else:
            self._engine.pause_recording()
            self._pause_btn.setText("▶ 继续")
            self._indicator.set_paused(True)

    def _on_stop(self):
        self._status_label.setText("正在保存录制...")
        self._engine.stop_recording()
        self._recording = False

        self._record_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("⏸ 暂停")
        self._stop_btn.setEnabled(False)
        self._indicator.set_recording(False)
        self._indicator.set_paused(False)
        self._duration_label.setText("00:00")
        if not self._engine.is_recording and self._engine.output_path:
            self._status_label.setText(f"已保存: {self._engine.output_path.name}")

        # 恢复模式/来源切换
        self._mode_screen_radio.setEnabled(True)
        self._mode_window_radio.setEnabled(True)
        window_mode = self._mode_window_radio.isChecked()
        self._monitor_combo.setEnabled(not window_mode)
        self._window_combo.setEnabled(True)
        self._window_refresh_btn.setEnabled(True)

    def _on_fps_changed(self, value: int):
        self._fps_label.setText(f"{value} FPS")

    def _on_select_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", str(self._output_dir))
        if path:
            self._output_dir = Path(path)
            self._path_label.setText(f"输出目录: {self._output_dir}")

    def _on_engine_status(self, status: str):
        self._status_label.setText(status)

    def _on_engine_frame(self, elapsed: float, frame_count: int):
        self._duration_label.setText(self._format_time(elapsed))
        self._start_time = elapsed

    def _on_engine_error(self, error: str):
        QMessageBox.critical(self, "录制错误", f"录制过程中出错:\n{error}")

    def _on_window_lost_ui(self):
        """窗口被关闭时自动停止录制（在主线程执行）"""
        self._recording = False
        self._record_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("⏸ 暂停")
        self._stop_btn.setEnabled(False)
        self._indicator.set_recording(False)
        self._indicator.set_paused(False)
        self._duration_label.setText("00:00")
        self._status_label.setText("目标窗口已关闭，录制已停止")

        # 恢复模式/来源切换
        self._mode_screen_radio.setEnabled(True)
        self._mode_window_radio.setEnabled(True)
        window_mode = self._mode_window_radio.isChecked()
        self._monitor_combo.setEnabled(not window_mode)
        self._window_combo.setEnabled(True)
        self._window_refresh_btn.setEnabled(True)

    def _on_recording_done(self, final_path: Path):
        """录制完成回调，显示最终文件路径"""
        self._status_label.setText(f"已保存: {final_path.name}")

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.raise_()

    def _update_ui(self):
        """定时更新UI"""
        if self._engine.is_recording and not self._engine.is_paused:
            elapsed = self._engine.get_duration()
            self._duration_label.setText(self._format_time(elapsed))

    def _update_preview(self):
        """刷新右侧预览画面。"""
        if not hasattr(self, "_preview_label"):
            return

        frame = None
        source_text = ""
        if self._mode_window_radio.isChecked():
            hwnd = self._window_combo.currentData()
            if hwnd is None:
                self._set_preview_message("请选择一个窗口")
                return
            frame = capture_window_content(hwnd, client_only=False)
            source_text = self._window_combo.currentText()
            if frame is None:
                if is_window_minimized(hwnd):
                    self._set_preview_message("窗口已最小化\n此应用当前没有可抓取的实时画面")
                else:
                    self._set_preview_message("窗口自身捕获失败\n该窗口可能使用硬件加速或不支持 PrintWindow")
                self._preview_info_label.setText(source_text)
                return
        else:
            frame = self._capture_monitor_preview()
            source_text = self._monitor_combo.currentText()
            if frame is None:
                self._set_preview_message("显示器预览失败")
                return

        self._show_preview_frame(frame)
        self._preview_info_label.setText(source_text)

    def _capture_monitor_preview(self) -> np.ndarray | None:
        try:
            monitors = self._preview_sct.monitors
            idx = self._monitor_combo.currentIndex() + 1
            monitor = monitors[idx] if 1 <= idx < len(monitors) else monitors[1]
            sct_img = self._preview_sct.grab(monitor)
            frame = np.asarray(sct_img)
            if frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            return frame
        except Exception:
            return None

    def _show_preview_frame(self, frame: np.ndarray):
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb = np.ascontiguousarray(rgb)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg).scaled(
                self._preview_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._preview_label.setPixmap(pixmap)
        except Exception as exc:
            self._set_preview_message(f"预览渲染失败\n{exc}")

    def _set_preview_message(self, message: str):
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText(message)

    @staticmethod
    def _format_time(seconds: float) -> str:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"

    # ── 窗口事件 ──────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent):
        if self._engine.is_recording:
            reply = QMessageBox.question(
                self, "确认退出",
                "录制正在进行中，确定要退出吗？\n退出将停止录制。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self._on_stop()
        event.accept()
        QApplication.quit()


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 全局样式
    app.setStyle("Fusion")

    win = RecorderWindow()
    win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
