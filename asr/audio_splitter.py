"""长音频切片工具（备用，阿里云异步模式一般不需要）。"""

from pathlib import Path

from loguru import logger
from pydub import AudioSegment


def split_audio(
    audio_path: Path,
    chunk_duration_ms: int = 60_000,
    overlap_ms: int = 1000,
    output_dir: Path | None = None,
) -> list[Path]:
    """
    将长音频切分为固定长度的片段。

    Args:
        audio_path: 输入音频文件路径
        chunk_duration_ms: 每片时长（毫秒），默认 60 秒
        overlap_ms: 片段之间的重叠时长（毫秒），避免切断单词
        output_dir: 输出目录，默认在 data/audio_chunks/

    Returns:
        切片文件路径列表
    """
    if output_dir is None:
        output_dir = Path("data/audio_chunks")
    output_dir.mkdir(parents=True, exist_ok=True)

    audio = AudioSegment.from_file(str(audio_path))
    total_ms = len(audio)
    logger.info(f"音频总时长: {total_ms / 1000:.1f}s，切片大小: {chunk_duration_ms / 1000:.0f}s")

    chunks = []
    start = 0
    idx = 0

    while start < total_ms:
        end = min(start + chunk_duration_ms, total_ms)
        chunk = audio[start:end]
        chunk_path = output_dir / f"{audio_path.stem}_chunk_{idx:04d}.wav"
        chunk.export(str(chunk_path), format="wav")
        chunks.append(chunk_path)
        start += chunk_duration_ms - overlap_ms
        idx += 1

    logger.info(f"音频切片完成: {len(chunks)} 个片段")
    return chunks
