"""声音克隆：从播客原始音频中自动提取声纹参考样本。"""

import hashlib
from pathlib import Path

from loguru import logger
from pydub import AudioSegment

from asr.base import TranscriptResult

# 声纹样本缓存目录
VOICE_SAMPLES_DIR = Path("data/voice_samples")


def extract_voice_sample(
    audio_path: Path,
    transcript: TranscriptResult,
    podcast_name: str,
    target_duration_ms: int = 15_000,
    min_segment_ms: int = 3000,
) -> Path:
    """
    从播客音频中自动提取一段清晰的说话片段作为声音克隆参考。

    策略：
    1. 利用 ASR 返回的时间戳，找到连续、较长的说话片段
    2. 优先选取音频前 30 分钟的片段（通常是主播自我介绍，声音最清晰）
    3. 拼接多个相邻片段直到达到目标时长（10-30 秒）
    4. 按播客名缓存，同一播客的不同 episode 复用声纹

    Args:
        audio_path: 下载的原始音频路径
        transcript: ASR 转录结果（含时间戳）
        podcast_name: 播客名称（用于缓存 key）
        target_duration_ms: 目标声纹样本时长（毫秒）
        min_segment_ms: 最短有效片段时长（毫秒）

    Returns:
        声纹参考音频路径（WAV 格式）
    """
    VOICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    # 检查是否已有该播客的声纹缓存
    cache_key = hashlib.md5(podcast_name.encode()).hexdigest()
    cached_path = VOICE_SAMPLES_DIR / f"{cache_key}_voice_sample.wav"
    if cached_path.exists():
        logger.info(f"使用缓存的声纹样本: {podcast_name}")
        return cached_path

    logger.info(f"从音频中提取声纹样本: {podcast_name}")
    audio = AudioSegment.from_file(str(audio_path))

    # 筛选前 30 分钟内较长的连续片段
    max_start_ms = 30 * 60 * 1000  # 前 30 分钟
    candidates = [
        seg
        for seg in transcript.segments
        if seg.start_ms < max_start_ms
        and (seg.end_ms - seg.start_ms) >= min_segment_ms
    ]

    if not candidates:
        # 放宽条件：使用所有片段
        candidates = [
            seg
            for seg in transcript.segments
            if (seg.end_ms - seg.start_ms) >= min_segment_ms
        ]

    if not candidates:
        # 最后兜底：使用前 15 秒
        logger.warning("未找到合适的声纹片段，使用音频前 15 秒")
        sample = audio[:target_duration_ms]
        sample = sample.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        sample.export(str(cached_path), format="wav")
        return cached_path

    # 拼接相邻片段直到达到目标时长
    collected = AudioSegment.empty()
    for seg in candidates:
        chunk = audio[seg.start_ms : seg.end_ms]
        collected += chunk
        if len(collected) >= target_duration_ms:
            break

    # 截取到目标时长
    if len(collected) > target_duration_ms:
        collected = collected[:target_duration_ms]

    # 转为 16kHz 单声道 WAV
    collected = collected.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    collected.export(str(cached_path), format="wav")

    logger.info(
        f"声纹样本提取完成: {len(collected) / 1000:.1f}s, 保存至 {cached_path.name}"
    )
    return cached_path
