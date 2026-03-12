"""音频拼接工具：将多个音频片段合并为一个文件。"""

from pathlib import Path

from loguru import logger
from pydub import AudioSegment


def merge_audio_files(
    audio_files: list[Path],
    output_path: Path,
    pause_duration_ms: int = 800,
) -> Path:
    """
    将多个音频片段拼接为一个完整文件。
    在段落之间插入静音间隔，模拟自然朗读的停顿。

    Args:
        audio_files: 音频文件路径列表
        output_path: 输出文件路径
        pause_duration_ms: 段落间停顿时长（毫秒）

    Returns:
        输出文件路径
    """
    combined = AudioSegment.empty()
    pause = AudioSegment.silent(duration=pause_duration_ms)

    for i, audio_file in enumerate(audio_files):
        segment = AudioSegment.from_file(str(audio_file))
        combined += segment
        if i < len(audio_files) - 1:
            combined += pause

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(output_path), format="mp3")
    logger.info(f"音频拼接完成: {len(audio_files)} 个片段 → {output_path.name} ({len(combined) / 1000:.1f}s)")
    return output_path
