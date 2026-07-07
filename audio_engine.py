"""音频录制引擎（Windows WASAPI Loopback / WDM-KS 立体声混音）

支持：
- 仅系统声音（游戏等应用输出）
- 系统声音 + 麦克风
"""

import subprocess
import shutil
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None


class AudioRecorder:
    """音频录制器"""

    MODE_NONE = "none"
    MODE_SYSTEM = "system"
    MODE_BOTH = "both"

    def __init__(self):
        self._recording = False
        self._mode = self.MODE_NONE
        self._sample_rate = 48000
        self._channels = 2
        self._threads: list[threading.Thread] = []
        self._system_chunks: list[np.ndarray] = []
        self._mic_chunks: list[np.ndarray] = []
        self._output_path: Path | None = None
        self._last_error: str = ""  # 记录最后一次错误
        self._pa = None
        self._system_sample_rate = self._sample_rate
        self._mic_sample_rate = self._sample_rate
        self._audio_start_perf = 0.0
        self._paused = False
        self._pause_started_perf = 0.0
        self._paused_total = 0.0
        self._system_sample_count = 0
        self._mic_sample_count = 0
        self._lock = threading.Lock()

    @property
    def last_error(self) -> str:
        return self._last_error

    def _set_error(self, message: str):
        if self._last_error:
            self._last_error += f"; {message}"
        else:
            self._last_error = message

    def _find_wasapi_hostapi(self) -> int | None:
        """查找 WASAPI hostapi 索引"""
        for i, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in api["name"].upper():
                return i
        return None

    def _find_loopback_device(self) -> int | None:
        """查找系统音频捕获设备（sounddevice 兜底方案）"""
        devices = sd.query_devices()

        # WDM-KS/MME 立体声混音。有些驱动会叫 Stereo input。
        for i, d in enumerate(devices):
            name = d["name"].lower()
            if d["max_input_channels"] >= 2:
                if (
                    "立体声混音" in name
                    or "stereo mix" in name
                    or "wave out mix" in name
                    or "stereo input" in name
                ):
                    return i

        return None

    def _find_pyaudio_loopback_device(self) -> dict | None:
        """查找 pyaudiowpatch 暴露的 WASAPI loopback 设备。"""
        if pyaudio is None:
            return None

        self._pa = pyaudio.PyAudio()
        try:
            default_speakers = self._pa.get_default_wasapi_loopback()
            if default_speakers:
                return default_speakers
        except Exception:
            pass

        try:
            for device in self._pa.get_loopback_device_info_generator():
                if device.get("maxInputChannels", 0) > 0:
                    return device
        except Exception:
            pass

        return None

    def _find_mic_device(self) -> int | None:
        """查找麦克风"""
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        def is_mic_device(device) -> bool:
            name = device["name"].lower()
            if device["max_input_channels"] <= 0:
                return False
            return (
                "立体声混音" not in name
                and "stereo mix" not in name
                and "stereo input" not in name
                and "loopback" not in name
            )

        try:
            default_input = sd.default.device[0]
            if default_input is not None and default_input >= 0:
                if is_mic_device(devices[default_input]):
                    return default_input
        except Exception:
            pass

        preferred_hostapi = ("WASAPI", "DirectSound", "MME")
        for api_name in preferred_hostapi:
            for i, d in enumerate(devices):
                hostapi_name = hostapis[d["hostapi"]]["name"]
                if api_name.lower() in hostapi_name.lower() and is_mic_device(d):
                    return i

        for i, d in enumerate(devices):
            if is_mic_device(d):
                return i
        return None

    def start_recording(self, output_path: Path, mode: str):
        if self._recording:
            return

        self._mode = mode
        self._output_path = output_path
        self._system_chunks = []
        self._mic_chunks = []
        self._recording = True
        self._threads = []
        self._last_error = ""
        self._pa = None
        self._system_sample_rate = self._sample_rate
        self._mic_sample_rate = self._sample_rate
        self._audio_start_perf = time.perf_counter()
        self._paused = False
        self._pause_started_perf = 0.0
        self._paused_total = 0.0
        self._system_sample_count = 0
        self._mic_sample_count = 0

        if mode in (self.MODE_SYSTEM, self.MODE_BOTH):
            loopback_dev = self._find_pyaudio_loopback_device()
            if loopback_dev is not None:
                t = threading.Thread(
                    target=self._record_system_loopback,
                    args=(loopback_dev,),
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
            else:
                stereo_dev = self._find_loopback_device()
                if stereo_dev is not None:
                    t = threading.Thread(
                        target=self._record_system_stereo_mix,
                        args=(stereo_dev,),
                        daemon=True,
                    )
                    t.start()
                    self._threads.append(t)
                else:
                    if pyaudio is None:
                        self._set_error("未安装 pyaudiowpatch，无法使用 WASAPI 系统声音捕获")
                    else:
                        self._set_error("未找到系统音频捕获设备")

        if mode == self.MODE_BOTH:
            mic_dev = self._find_mic_device()
            if mic_dev is not None:
                t = threading.Thread(
                    target=self._record_mic,
                    args=(mic_dev,),
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
            else:
                self._set_error("未找到麦克风设备")

    def stop_recording(self) -> Path | None:
        if not self._recording:
            return None

        self._recording = False
        for t in self._threads:
            t.join(timeout=3)
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

        if not self._system_chunks and not self._mic_chunks:
            self._set_error("未捕获到任何音频数据")
            return None

        try:
            return self._save_audio()
        except Exception as e:
            self._set_error(f"保存音频失败: {e}")
            return None

    def pause_recording(self):
        """暂停音频时间轴。"""
        with self._lock:
            if self._recording and not self._paused:
                self._paused = True
                self._pause_started_perf = time.perf_counter()

    def resume_recording(self):
        """恢复音频时间轴。"""
        with self._lock:
            if self._recording and self._paused:
                self._paused_total += time.perf_counter() - self._pause_started_perf
                self._paused = False
                self._pause_started_perf = 0.0

    def _active_elapsed(self, now: float | None = None) -> float:
        now = now or time.perf_counter()
        paused_total = self._paused_total
        if self._paused:
            paused_total += now - self._pause_started_perf
        return max(0.0, now - self._audio_start_perf - paused_total)

    def _append_timed_chunk(self, target: str, chunk: np.ndarray, sample_rate: int):
        """按录制时间轴追加音频块，自动补齐前置静音。"""
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, 1)
        chunk = chunk.astype(np.float32, copy=False)

        with self._lock:
            if not self._recording or self._paused:
                return

            chunks = self._system_chunks if target == "system" else self._mic_chunks
            count_attr = "_system_sample_count" if target == "system" else "_mic_sample_count"
            current_count = getattr(self, count_attr)
            chunk_len = len(chunk)
            expected_end = int(round(self._active_elapsed() * sample_rate))
            missing = expected_end - current_count - chunk_len

            if missing > 0:
                chunks.append(np.zeros((missing, chunk.shape[1]), dtype=np.float32))
                current_count += missing

            chunks.append(chunk.copy())
            setattr(self, count_attr, current_count + chunk_len)

    def _record_system_loopback(self, device_info: dict):
        """使用 pyaudiowpatch 录制系统声音。"""
        try:
            channels = min(int(device_info.get("maxInputChannels", 2)) or 2, self._channels)
            rate = int(device_info.get("defaultSampleRate") or self._sample_rate)
            self._system_sample_rate = rate
            stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=1024,
            )
            try:
                while self._recording:
                    data = stream.read(1024, exception_on_overflow=False)
                    chunk = np.frombuffer(data, dtype=np.float32).reshape(-1, channels)
                    self._append_timed_chunk("system", chunk, rate)
            finally:
                stream.stop_stream()
                stream.close()
        except Exception as e:
            self._set_error(f"系统音频录制错误: {e}")

    def _record_system_stereo_mix(self, device_id: int):
        """使用立体声混音类输入设备录制系统声音。"""
        try:
            dev_info = sd.query_devices(device_id)
            channels = min(dev_info["max_input_channels"] or 2, self._channels)
            rate = int(dev_info["default_samplerate"] or self._sample_rate)
            self._system_sample_rate = rate

            def callback(indata, _frames, _time_info, _status):
                self._append_timed_chunk("system", indata, rate)

            with sd.InputStream(
                device=device_id,
                channels=channels,
                samplerate=rate,
                callback=callback,
            ):
                while self._recording:
                    time.sleep(0.1)

        except Exception as e:
            self._set_error(f"系统音频录制错误: {e}")

    def _record_mic(self, device_id: int):
        """录制麦克风"""
        try:
            dev_info = sd.query_devices(device_id)
            channels = min(dev_info["max_input_channels"], 1)  # 麦克风单声道
            rate = int(dev_info["default_samplerate"] or self._sample_rate)
            self._mic_sample_rate = rate

            def callback(indata, _frames, _time_info, _status):
                self._append_timed_chunk("mic", indata, rate)

            with sd.InputStream(
                device=device_id,
                channels=channels,
                samplerate=rate,
                callback=callback,
            ):
                while self._recording:
                    time.sleep(0.1)

        except Exception as e:
            self._set_error(f"麦克风录制错误: {e}")

    def _save_audio(self) -> Path | None:
        if self._output_path is None:
            return None

        wav_path = self._output_path.with_suffix(".wav")

        if self._system_chunks:
            system_data = np.concatenate(self._system_chunks, axis=0)
            system_data = self._resample_audio(system_data, self._system_sample_rate, self._sample_rate)
            system_data = self._normalize_channels(system_data, self._channels)
        else:
            system_data = np.array([]).reshape(0, 2)

        if len(system_data) == 0 and not self._mic_chunks:
            return None

        # 混合系统音频和麦克风
        if self._mic_chunks and len(system_data) > 0:
            mic_data = np.concatenate(self._mic_chunks, axis=0)
            mic_data = self._resample_audio(mic_data, self._mic_sample_rate, self._sample_rate)
            mic_data = self._normalize_channels(mic_data, system_data.shape[1])

            min_len = min(len(system_data), len(mic_data))
            mixed = system_data[:min_len].astype(np.float64) * 0.7 + mic_data[:min_len].astype(np.float64) * 0.5
            mixed = np.clip(mixed, -1.0, 1.0).astype(np.float32)
            final_data = mixed
        elif len(system_data) > 0:
            final_data = system_data.astype(np.float32)
        elif self._mic_chunks:
            mic_data = np.concatenate(self._mic_chunks, axis=0)
            mic_data = self._resample_audio(mic_data, self._mic_sample_rate, self._sample_rate)
            final_data = self._normalize_channels(mic_data, 1).astype(np.float32)
        else:
            return None

        # 写入 WAV
        int_data = np.clip(final_data * 32767, -32768, 32767).astype(np.int16)
        n_channels = int_data.shape[1] if int_data.ndim == 2 else 1

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(int_data.tobytes())

        return wav_path

    @staticmethod
    def _normalize_channels(data: np.ndarray, channels: int) -> np.ndarray:
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if data.shape[1] == channels:
            return data
        if data.shape[1] > channels:
            return data[:, :channels]
        return np.repeat(data, channels, axis=1)

    @staticmethod
    def _resample_audio(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        if source_rate == target_rate or len(data) == 0:
            return data
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        target_len = max(1, int(round(len(data) * target_rate / source_rate)))
        old_x = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
        new_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        channels = [
            np.interp(new_x, old_x, data[:, ch]).astype(np.float32)
            for ch in range(data.shape[1])
        ]
        return np.stack(channels, axis=1)


def _find_ffmpeg() -> str | None:
    """优先使用 PyInstaller 内置/同目录 ffmpeg，其次使用系统 PATH。"""
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    candidate_dirs = []
    if getattr(sys, "frozen", False):
        candidate_dirs.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        candidate_dirs.append(Path(sys.executable).parent)
    else:
        candidate_dirs.append(Path(__file__).parent)

    for directory in candidate_dirs:
        ffmpeg = directory / exe_name
        if ffmpeg.exists():
            return str(ffmpeg)
    return shutil.which("ffmpeg")


def merge_audio_video(video_path: Path, audio_path: Path, retries: int = 5, retry_delay: float = 1.0) -> Path | None:
    """将音频合入视频（ffmpeg）"""
    merged_path = video_path.with_stem(video_path.stem + "_有声音")
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        return None

    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW

    for attempt in range(max(1, retries)):
        try:
            result = subprocess.run(
                [
                    ffmpeg, "-y",
                    "-i", str(video_path),
                    "-i", str(audio_path),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-shortest",
                    str(merged_path),
                ],
                capture_output=True,
                timeout=120,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            if result.returncode == 0 and merged_path.exists():
                return merged_path
        except FileNotFoundError:
            return None
        except Exception:
            pass

        if attempt < retries - 1:
            time.sleep(retry_delay)

    return None
