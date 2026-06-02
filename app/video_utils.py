import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.schemas import VideoInfo

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}


class FFmpegNotFoundError(RuntimeError):
    pass


class VideoProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoSegment:
    index: int
    path: Path
    start_seconds: float
    end_seconds: float

    @property
    def start_time(self) -> str:
        return seconds_to_hhmmss(self.start_seconds)

    @property
    def end_time(self) -> str:
        return seconds_to_hhmmss(self.end_seconds)


def ensure_ffmpeg() -> None:
    missing = [cmd for cmd in ("ffmpeg", "ffprobe") if shutil.which(cmd) is None]
    if missing:
        raise FFmpegNotFoundError(
            "找不到 FFmpeg/ffprobe。請先安裝 FFmpeg，並確認 ffmpeg 與 ffprobe 已加入 PATH。"
        )


def seconds_to_hhmmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def seconds_to_timecode(seconds: float, include_ms: bool = True) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    if include_ms:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def hhmmss_to_seconds(value: str) -> float:
    cleaned = re.sub(r"^[\[\(【（]\s*|\s*[\]\)】）]$", "", value.strip())
    if not cleaned:
        raise ValueError("時間格式不可為空")
    parts = cleaned.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"時間格式需為 HH:MM:SS 或 MM:SS：{value}")


def clamp_seconds(seconds: float, duration_seconds: float | None = None) -> float:
    value = max(0.0, seconds)
    if duration_seconds is not None and duration_seconds > 0:
        return min(value, duration_seconds)
    return value


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError(
            "找不到 FFmpeg/ffprobe。請先安裝 FFmpeg，並確認 ffmpeg 與 ffprobe 已加入 PATH。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise VideoProcessingError(detail) from exc


def _parse_fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            den = float(denominator)
            return float(numerator) / den if den else None
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def get_video_info(video_path: Path) -> VideoInfo:
    if not video_path.exists():
        raise FileNotFoundError(f"找不到影片檔：{video_path}")
    ensure_ffmpeg()

    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
    )
    data = json.loads(result.stdout)
    format_info = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})

    duration_seconds = float(format_info.get("duration") or 0)
    file_size_bytes = video_path.stat().st_size

    return VideoInfo(
        file_name=video_path.name,
        file_path=str(video_path.resolve()),
        duration_seconds=duration_seconds,
        duration=seconds_to_hhmmss(duration_seconds),
        file_size_bytes=file_size_bytes,
        file_size_mb=round(file_size_bytes / (1024 * 1024), 2),
        width=video_stream.get("width"),
        height=video_stream.get("height"),
        fps=_parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        audio={
            "codec": audio_stream.get("codec_name"),
            "sample_rate": audio_stream.get("sample_rate"),
            "channels": audio_stream.get("channels"),
            "channel_layout": audio_stream.get("channel_layout"),
        }
        if audio_stream
        else {},
    )


def split_video(video_path: Path, output_dir: Path, chunk_minutes: int, duration_seconds: float) -> list[VideoSegment]:
    if chunk_minutes <= 0:
        raise ValueError("--chunk-minutes 必須大於 0")
    ensure_ffmpeg()
    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_seconds = chunk_minutes * 60
    segments: list[VideoSegment] = []
    stem = video_path.stem
    start = 0.0
    index = 1

    while start < duration_seconds or (duration_seconds == 0 and index == 1):
        end = min(start + chunk_seconds, duration_seconds) if duration_seconds else start + chunk_seconds
        is_audio = video_path.suffix.lower() in AUDIO_EXTENSIONS
        part_path = output_dir / f"{stem}_part_{index:03d}{'.wav' if is_audio else '.mp4'}"
        if is_audio:
            command = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                str(video_path),
                "-t",
                str(chunk_seconds),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(part_path),
            ]
        else:
            command = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                str(video_path),
                "-t",
                str(chunk_seconds),
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                str(part_path),
            ]
        _run(command)
        if not part_path.exists() or part_path.stat().st_size == 0:
            raise VideoProcessingError(f"影片切段失敗，未產生有效檔案：{part_path}")
        segments.append(VideoSegment(index=index, path=part_path, start_seconds=start, end_seconds=end))
        index += 1
        start += chunk_seconds
        if duration_seconds == 0:
            break

    return segments


def extract_audio(video_path: Path, output_path: Path) -> Path:
    ensure_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VideoProcessingError(f"音訊抽取失敗，未產生有效檔案：{output_path}")
    return output_path


def create_proxy_video(video_path: Path, output_path: Path, max_width: int = 1280) -> Path:
    ensure_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if video_path.suffix.lower() in AUDIO_EXTENSIONS:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(output_path),
            ]
        )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise VideoProcessingError(f"音頻 proxy 產生失敗：{output_path}")
        return output_path
    scale = f"scale='min({max_width},iw)':-2"
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            scale,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_path),
        ]
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VideoProcessingError(f"proxy 影片產生失敗：{output_path}")
    return output_path


def create_clip(video_path: Path, output_path: Path, start_time: str, end_time: str) -> Path:
    ensure_ffmpeg()
    start_seconds = hhmmss_to_seconds(start_time)
    end_seconds = hhmmss_to_seconds(end_time)
    duration = max(0.1, end_seconds - start_seconds)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if video_path.suffix.lower() in AUDIO_EXTENSIONS:
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_seconds),
            "-i",
            str(video_path),
            "-t",
            str(duration),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_seconds),
            "-i",
            str(video_path),
            "-t",
            str(duration),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ]
    _run(command)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VideoProcessingError(f"短片草稿產生失敗：{output_path}")
    return output_path
