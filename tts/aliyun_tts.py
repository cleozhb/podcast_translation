"""阿里云 CosyVoice 音色克隆 TTS。"""

import json
import re
import time
from pathlib import Path

import requests
from loguru import logger

from tts.base import BaseTTS
from utils.retry import retry


class AliyunCosyVoiceTTS(BaseTTS):
    """
    阿里云 CosyVoice 语音合成（支持音色克隆）。

    CosyVoice 支持 zero-shot 音色克隆：
    - 传入一段参考音频（10-30 秒），即可生成相似音色的语音
    - 不需要训练/微调，实时生成

    对于长文本：
    - 按段落分段合成
    - 每段独立调用 API
    - 最后拼接所有片段
    """

    # DashScope CosyVoice API (通过 dashscope SDK 调用)
    def __init__(self, api_key: str, model: str = "cosyvoice-v1"):
        self.api_key = api_key
        self.model = model

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_sample_path: Path | None = None,
    ) -> Path:
        """合成语音，支持声音克隆。"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 按段落分段
        paragraphs = self._split_text(text)
        logger.info(f"TTS: {len(paragraphs)} 个段落, 声音克隆: {voice_sample_path is not None}")

        # 逐段合成
        audio_parts = []
        for i, para in enumerate(paragraphs):
            if not para.strip():
                continue
            logger.info(f"合成第 {i + 1}/{len(paragraphs)} 段 ({len(para)} 字)...")
            part_path = output_path.parent / f"{output_path.stem}_part_{i:04d}.mp3"
            self._synthesize_segment(para, part_path, voice_sample_path)
            audio_parts.append(part_path)

        # 拼接
        if len(audio_parts) == 1:
            audio_parts[0].rename(output_path)
        else:
            from tts.audio_merger import merge_audio_files
            merge_audio_files(audio_parts, output_path)
            # 清理临时文件
            for part in audio_parts:
                if part.exists():
                    part.unlink()

        logger.info(f"TTS 合成完成: {output_path}")
        return output_path

    @retry(max_retries=3)
    def _synthesize_segment(
        self, text: str, output_path: Path, voice_sample_path: Path | None
    ):
        """合成单个段落。"""
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = self.api_key

        synthesizer_params = {
            "model": self.model,
            "text": text,
        }

        if voice_sample_path:
            # 声音克隆模式：传入参考音频
            import base64

            with open(voice_sample_path, "rb") as f:
                audio_data = base64.b64encode(f.read()).decode()
            synthesizer_params["voice"] = "clone"
            synthesizer_params["reference_audio"] = audio_data
        else:
            # 使用默认中文音色
            synthesizer_params["voice"] = "longxiaochun"

        synthesizer = SpeechSynthesizer(**synthesizer_params)
        audio = synthesizer.call(text)

        with open(output_path, "wb") as f:
            f.write(audio)

    def _split_text(self, text: str, max_chars: int = 500) -> list[str]:
        """按句子/段落边界分段，每段不超过 max_chars 字符。"""
        # 先按段落分
        raw_paragraphs = text.split("\n\n")
        result = []

        for para in raw_paragraphs:
            if len(para) <= max_chars:
                result.append(para)
            else:
                # 长段落按句子边界切分
                sentences = re.split(r"(?<=[。！？；])", para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) > max_chars and current:
                        result.append(current)
                        current = sent
                    else:
                        current += sent
                if current:
                    result.append(current)

        return result
