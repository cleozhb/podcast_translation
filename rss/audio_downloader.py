"""音频下载模块：流式下载播客音频 + 格式转换。"""

import hashlib
from pathlib import Path

import requests
from loguru import logger
from pydub import AudioSegment
from tqdm import tqdm

from utils.retry import retry

HEADERS = {"User-Agent": "PodcastTranslator/1.0"}


@retry(max_retries=3)
def download_audio(url: str, episode_guid: str, output_dir: Path) -> Path:
    """
    流式下载播客音频文件。

    使用 episode guid 的 hash 作为文件名，避免重复下载。
    支持断点续传（简化版：文件存在且大小匹配则跳过）。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = hashlib.md5(episode_guid.encode()).hexdigest()

    # 探测文件类型和大小
    head_resp = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=30)
    content_type = head_resp.headers.get("Content-Type", "")
    total_size = int(head_resp.headers.get("Content-Length", 0))

    ext_map = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
    }
    ext = ext_map.get(content_type, ".mp3")
    output_path = output_dir / f"{safe_name}{ext}"

    # 跳过已下载的文件
    if output_path.exists():
        existing_size = output_path.stat().st_size
        if total_size == 0 or existing_size == total_size:
            logger.info(f"音频已存在，跳过下载: {output_path.name}")
            return output_path

    logger.info(f"开始下载音频: {url[:80]}...")
    resp = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    resp.raise_for_status()

    with open(output_path, "wb") as f:
        with tqdm(total=total_size, unit="B", unit_scale=True, desc="下载") as pbar:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))

    logger.info(f"下载完成: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return output_path


def convert_to_wav(input_path: Path, sample_rate: int = 16000) -> Path:
    """将音频转换为 WAV 格式（16kHz, 单声道, 16bit PCM），适用于 ASR。"""
    output_path = input_path.with_suffix(".wav")
    if output_path.exists():
        logger.info(f"WAV 文件已存在: {output_path.name}")
        return output_path

    logger.info(f"转换格式: {input_path.name} → WAV (16kHz)")
    audio = AudioSegment.from_file(str(input_path))
    audio = audio.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    audio.export(str(output_path), format="wav")
    logger.info(f"格式转换完成: {output_path.name}")
    return output_path
