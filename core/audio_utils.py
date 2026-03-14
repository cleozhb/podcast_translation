"""
core/audio_utils.py
===================
音频预处理工具：下载、格式转换、声纹提取
"""

import os
import re
import requests
from pydub import AudioSegment, silence


def download_audio(url: str, output_dir: str, filename: str = None,
                   timeout: int = 120, proxy: str = None) -> str:
    """
    下载播客音频文件。

    Returns:
        本地文件路径
    """
    if not filename:
        # 从 URL 提取文件名
        name = url.split("/")[-1].split("?")[0]
        if not name or "." not in name:
            name = "episode.mp3"
        filename = name

    # 清理文件名
    filename = re.sub(r'[\\/*?:"<>|]', '_', filename)[:100]
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        print(f"  📁 文件已存在，跳过下载: {filepath}")
        return filepath

    print(f"  📥 下载音频: {url[:80]}...")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0 (PodcastTranslator/1.0)"}

    resp = requests.get(url, stream=True, timeout=timeout,
                        headers=headers, proxies=proxies, allow_redirects=True)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = downloaded / total * 100
                print(f"\r     进度: {pct:.1f}% ({downloaded // (1024*1024)}MB / {total // (1024*1024)}MB)", end="")

    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"\n  ✅ 下载完成: {filepath} ({size_mb:.1f}MB)")
    return filepath


def extract_voiceprint(
    audio_path: str,
    output_dir: str,
    start_sec: int = 60,
    duration_sec: int = 20,
    target_sample_rate: int = 16000,
) -> str:
    """
    从音频中提取声纹片段。
    跳过开头（通常有 intro 音乐），截取一段干净的人声。

    Args:
        audio_path: 源音频路径
        output_dir: 输出目录
        start_sec: 起始秒数（跳过 intro）
        duration_sec: 截取时长
        target_sample_rate: 目标采样率

    Returns:
        声纹音频文件路径
    """
    print(f"  🎵 提取声纹片段: 从 {start_sec}s 开始，截取 {duration_sec}s")

    audio = AudioSegment.from_file(audio_path)
    total_sec = len(audio) / 1000

    # 确保起始点不超出范围
    if start_sec >= total_sec:
        start_sec = max(0, int(total_sec * 0.1))  # 取 10% 位置
        print(f"     ⚠️ 起始点超出范围，调整为 {start_sec}s")

    end_sec = min(start_sec + duration_sec, total_sec)
    clip = audio[start_sec * 1000 : int(end_sec * 1000)]

    # 转为单声道、目标采样率
    clip = clip.set_channels(1).set_frame_rate(target_sample_rate)

    # 输出为 WAV（声纹通常用 WAV）
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    output_path = os.path.join(output_dir, f"{basename}_voiceprint.wav")
    os.makedirs(output_dir, exist_ok=True)

    clip.export(output_path, format="wav")
    print(f"  ✅ 声纹片段: {output_path} ({end_sec - start_sec:.1f}s)")
    return output_path


def get_audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）"""
    audio = AudioSegment.from_file(audio_path)
    return len(audio) / 1000


def convert_to_wav(audio_path: str, output_dir: str,
                   sample_rate: int = 16000) -> str:
    """将音频转为 WAV 格式（部分 STT 服务需要）"""
    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_channels(1).set_frame_rate(sample_rate)

    basename = os.path.splitext(os.path.basename(audio_path))[0]
    output_path = os.path.join(output_dir, f"{basename}.wav")
    os.makedirs(output_dir, exist_ok=True)

    audio.export(output_path, format="wav")
    return output_path