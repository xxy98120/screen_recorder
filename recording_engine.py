"""屏幕录制引擎"""

import ctypes
import threading
import time
from datetime import datetime
from pathlib import Path
from ctypes import wintypes

import cv2
import mss
import numpy as np
from window_capture import capture_window_content
from window_utils import get_window_rect, is_window_minimized
from audio_engine import AudioRecorder, merge_audio_video


class RecordingEngine:
    """录制引擎 - 负责屏幕捕获和视频编码"""

    # 录制模式
    MODE_MONITOR = "monitor"  # 全屏录制
    MODE_WINDOW = "window"    # 窗口录制

    def __init__(self):
        self._recording = False
        self._paused = False
        self._thread: threading.Thread | None = None
        self._sct = mss.mss()
        self._monitor = self._sct.monitors[1]  # 默认主显示器
        self._mode = self.MODE_MONITOR
        self._target_hwnd: int | None = None   # 目标窗口句柄（窗口模式）
        self._window_capture_offset = (0, 0)   # 窗口在屏幕上的偏移（用于鼠标坐标）
        self._output_path: Path | None = None
        self._final_output_path: Path | None = None  # 合并音频后的最终路径
        self._fps = 30
        self._video_writer: cv2.VideoWriter | None = None
        self._writer_size: tuple[int, int] | None = None
        self._show_mouse = True
        self._frame_count = 0
        self._start_time: float = 0.0
        self._elapsed_paused = 0.0
        self._pause_start = 0.0
        self._last_frame_notify = 0.0

        # 音频
        self._audio_recorder = AudioRecorder()
        self._audio_mode = AudioRecorder.MODE_NONE

        # 回调
        self.on_status_changed = None  # func(status: str)
        self.on_frame_updated = None   # func(elapsed: float, frame_count: int)
        self.on_error = None           # func(error: str)
        self.on_window_lost = None     # func() 窗口被关闭
        self.on_recording_done = None  # func(final_path: Path) 录制完成

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def fps(self) -> int:
        return self._fps

    @fps.setter
    def fps(self, value: int):
        self._fps = max(1, min(60, value))

    @property
    def show_mouse(self) -> bool:
        return self._show_mouse

    @show_mouse.setter
    def show_mouse(self, value: bool):
        self._show_mouse = value

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def audio_mode(self) -> str:
        return self._audio_mode

    @audio_mode.setter
    def audio_mode(self, value: str):
        self._audio_mode = value

    @property
    def output_path(self) -> Path | None:
        """获取最终输出文件路径（可能是合并了音频的）"""
        return self._final_output_path or self._output_path

    def set_monitor(self, monitor_index: int):
        """设置录制的显示器"""
        monitors = self._sct.monitors
        if 1 <= monitor_index < len(monitors):
            self._monitor = monitors[monitor_index]
        else:
            self._monitor = monitors[1]
        self._mode = self.MODE_MONITOR

    def set_window(self, hwnd: int):
        """设置录制的目标窗口"""
        self._target_hwnd = hwnd
        self._mode = self.MODE_WINDOW
        # 初始化窗口偏移（用于鼠标坐标转换）
        rect = get_window_rect(hwnd)
        if rect:
            self._window_capture_offset = (rect[0], rect[1])

    def _notify_status(self, status: str):
        if self.on_status_changed:
            self.on_status_changed(status)

    def _notify_frame(self):
        if self.on_frame_updated:
            elapsed = 0.0
            if self._start_time > 0:
                total = time.time() - self._start_time - self._elapsed_paused
                elapsed = max(0.0, total)
            self.on_frame_updated(elapsed, self._frame_count)

    def start_recording(self, output_dir: str | Path):
        """开始录制"""
        if self._recording:
            return

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 文件名标注模式
        if self._mode == self.MODE_WINDOW and self._target_hwnd:
            stem = f"窗口录制_{timestamp}"
        else:
            stem = f"屏幕录制_{timestamp}"
        self._output_path = output_dir / f"{stem}.mp4"
        self._final_output_path = None

        self._recording = True
        self._paused = False
        self._frame_count = 0
        self._elapsed_paused = 0.0
        self._last_frame_notify = 0.0
        self._start_time = time.time()

        # 启动音频录制
        if self._audio_mode != AudioRecorder.MODE_NONE:
            audio_path = output_dir / f"{stem}_audio.wav"
            self._audio_recorder.start_recording(audio_path, self._audio_mode)

        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()

    def stop_recording(self):
        """停止录制"""
        self._recording = False
        if self._thread:
            self._thread.join(timeout=8)
            if self._thread.is_alive():
                self._notify_status("正在结束录制...")
                return
            self._thread = None

    def pause_recording(self):
        """暂停录制"""
        if not self._recording or self._paused:
            return
        self._paused = True
        self._pause_start = time.time()
        if self._audio_mode != AudioRecorder.MODE_NONE:
            self._audio_recorder.pause_recording()

    def resume_recording(self):
        """恢复录制"""
        if not self._recording or not self._paused:
            return
        self._elapsed_paused += time.time() - self._pause_start
        self._paused = False
        if self._audio_mode != AudioRecorder.MODE_NONE:
            self._audio_recorder.resume_recording()

    def _release_writer(self):
        if self._video_writer:
            self._video_writer.release()
            self._video_writer = None
        self._writer_size = None

    def _init_writer(self, width: int, height: int):
        """初始化视频写入器"""
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer_size = (width, height)
        self._video_writer = cv2.VideoWriter(
            str(self._output_path),
            fourcc,
            self._fps,
            (width, height),
        )
        if not self._video_writer.isOpened():
            raise RuntimeError("视频写入器初始化失败，请检查输出目录权限或编码器支持")

    def _normalize_frame_size(self, frame: np.ndarray) -> np.ndarray:
        """保持每一帧尺寸一致，避免窗口大小变化导致输出卡顿或损坏。"""
        if self._writer_size is None:
            return frame
        width, height = self._writer_size
        if frame.shape[1] == width and frame.shape[0] == height:
            return frame
        return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

    def _draw_mouse(self, frame: np.ndarray, offset_x: int = 0, offset_y: int = 0):
        """在帧上绘制鼠标指针"""
        try:
            point = wintypes.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return
            x, y = point.x, point.y
            rx, ry = int(x) - offset_x, int(y) - offset_y
            if 0 <= rx < frame.shape[1] and 0 <= ry < frame.shape[0]:
                cv2.circle(frame, (rx, ry), 8, (255, 50, 50), 2)
                cv2.drawMarker(frame, (rx, ry), (255, 50, 50), cv2.MARKER_CROSS, 12, 2)
        except Exception:
            pass

    def _grab_region(self, region: dict[str, int]) -> np.ndarray | None:
        """使用 mss 快速抓取区域，返回 BGR 帧。"""
        if region["width"] <= 0 or region["height"] <= 0:
            return None
        sct_img = self._sct.grab(region)
        frame = np.asarray(sct_img)
        if frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        return frame

    def _capture_monitor_frame(self) -> np.ndarray:
        return self._grab_region(self._monitor)

    def _capture_window_frame(self) -> np.ndarray | None:
        if self._target_hwnd is None:
            return None

        rect = get_window_rect(self._target_hwnd)
        if rect is None:
            return None

        self._window_capture_offset = (rect[0], rect[1])
        region = {
            "left": rect[0],
            "top": rect[1],
            "width": rect[2] - rect[0],
            "height": rect[3] - rect[1],
        }

        frame = capture_window_content(self._target_hwnd, client_only=False)
        if frame is not None:
            return frame

        if is_window_minimized(self._target_hwnd):
            return None

        try:
            frame = self._grab_region(region)
            if frame is not None:
                return frame
        except Exception:
            pass

        return None

    def _write_frame(self, frame: np.ndarray):
        if self._video_writer is None:
            h, w = frame.shape[:2]
            self._init_writer(w, h)
        frame = self._normalize_frame_size(frame)
        self._video_writer.write(frame)
        self._frame_count += 1

    def _record_loop(self):
        """录制主循环"""
        try:
            self._notify_status("录制中...")
            window_check_interval = 0.5  # 每0.5秒检查窗口状态
            last_window_check = 0.0
            frame_interval = 1.0 / max(1, self._fps)
            next_frame_at = time.perf_counter()

            while self._recording:
                if self._paused:
                    self._notify_status("已暂停")
                    time.sleep(0.1)
                    next_frame_at = time.perf_counter()
                    continue

                now_perf = time.perf_counter()
                if now_perf < next_frame_at:
                    time.sleep(next_frame_at - now_perf)
                next_frame_at += frame_interval

                if self._mode == self.MODE_WINDOW:
                    now = time.time()
                    if now - last_window_check >= window_check_interval:
                        rect = get_window_rect(self._target_hwnd)
                        if rect is None:
                            self._notify_status("目标窗口已关闭")
                            self._recording = False
                            self._window_lost_flag = True
                            break
                        self._window_capture_offset = (rect[0], rect[1])
                        last_window_check = now

                    if self._target_hwnd is None:
                        time.sleep(0.05)
                        continue

                    frame = self._capture_window_frame()
                    if frame is None:
                        time.sleep(0.05)
                        continue
                else:
                    frame = self._capture_monitor_frame()

                if frame is None:
                    continue

                # 绘制鼠标
                if self._show_mouse:
                    offset_x, offset_y = self._window_capture_offset if self._mode == self.MODE_WINDOW else (0, 0)
                    # 全屏模式用显示器偏移
                    if self._mode == self.MODE_MONITOR:
                        mon = self._monitor
                        offset_x, offset_y = mon["left"], mon["top"]
                    self._draw_mouse(frame, offset_x, offset_y)

                self._write_frame(frame)

                # 如果抓屏或编码跟不上，用少量重复帧补齐时间轴，避免成片快放/音画不同步。
                elapsed = self.get_duration()
                expected_frames = int(elapsed * self._fps)
                duplicate_count = 0
                while self._frame_count < expected_frames and duplicate_count < 2:
                    self._write_frame(frame)
                    duplicate_count += 1

                if time.perf_counter() - next_frame_at > frame_interval * 3:
                    next_frame_at = time.perf_counter() + frame_interval

                # 节流 UI 更新：最多每 100ms 刷新一次
                now = time.time()
                if now - self._last_frame_notify >= 0.1:
                    self._notify_frame()
                    self._last_frame_notify = now

            # 循环结束：释放视频资源
            self._release_writer()
            self._recording = False

            # 窗口丢失时也需要停止音频并合并
            self._stop_audio_and_merge()

            if getattr(self, '_window_lost_flag', False):
                self._window_lost_flag = False
                self._notify_status("窗口已关闭，录制已停止")
                if self.on_window_lost:
                    self.on_window_lost()
            else:
                self._notify_status("录制完成")

            self._notify_frame()
            if self.on_recording_done and self.output_path:
                self.on_recording_done(self.output_path)

        except Exception as e:
            self._release_writer()
            self._record_cleanup_audio()
            self._notify_status("录制出错")
            if self.on_error:
                self.on_error(str(e))

    def _stop_audio_and_merge(self):
        """停止音频录制并合并到视频"""
        if self._audio_mode == AudioRecorder.MODE_NONE:
            return
        audio_wav = self._audio_recorder.stop_recording()
        audio_err = self._audio_recorder.last_error
        if audio_wav and audio_wav.exists() and self._output_path:
            self._notify_status("正在合并音频...")
            merged = merge_audio_video(self._output_path, audio_wav)
            if merged:
                self._final_output_path = merged
                try:
                    audio_wav.unlink()
                except Exception:
                    pass
            else:
                self._notify_status("音频合并失败，请检查 ffmpeg；已保留 WAV 音频文件")
        elif audio_err:
            self._notify_status(f"音频录制失败: {audio_err}")

    def _record_cleanup_audio(self):
        """异常时清理音频"""
        try:
            self._audio_recorder.stop_recording()
        except Exception:
            pass

    def get_duration(self) -> float:
        """获取当前录制时长（秒）"""
        if self._start_time == 0:
            return 0.0
        total = time.time() - self._start_time - self._elapsed_paused
        return max(0.0, total)
