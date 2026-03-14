"""
core/pipeline.py
================
工作流编排引擎：串联 STT → LLM → TTS 各步骤
"""

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from providers.base import (
    STTProvider, LLMProvider, TTSProvider, StorageProvider,
    TranscriptResult, TranslationResult, TTSResult,
)
from core.audio_utils import download_audio, extract_voiceprint


@dataclass
class PipelineContext:
    """工作流上下文，记录每一步的输入输出"""
    # 输入
    podcast_name: str = ""
    episode_title: str = ""
    audio_url: str = ""
    rss_url: str = ""

    # Step 1: 下载
    local_audio_path: str = ""

    # Step 2: 声纹
    voiceprint_local_path: str = ""
    voiceprint_oss_url: str = ""

    # Step 3: STT
    transcript: Optional[TranscriptResult] = None
    transcript_path: str = ""

    # Step 4: 翻译
    translation: Optional[TranslationResult] = None
    translation_path: str = ""

    # Step 5: TTS
    tts_result: Optional[TTSResult] = None
    final_audio_path: str = ""

    # 元数据
    start_time: float = 0.0
    step_times: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


class Pipeline:
    """
    播客翻译工作流。

    用法:
        pipeline = Pipeline(config, stt, llm, tts, storage)
        result = pipeline.run(audio_url="...", podcast_name="...")
    """

    def __init__(
        self,
        config: dict,
        stt: STTProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        storage: Optional[StorageProvider] = None,
    ):
        self.config = config
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.storage = storage

        # 输出目录
        out = config.get("output", {})
        self.audio_dir = out.get("audio_dir", "./output/audio")
        self.transcript_dir = out.get("transcript_dir", "./output/transcripts")
        self.translation_dir = out.get("translation_dir", "./output/translations")
        self.voiceprint_dir = out.get("voiceprint_dir", "./output/voiceprints")
        self.final_dir = out.get("final_dir", "./output/final")

        # 翻译配置
        tc = config.get("translation", {})
        self.system_prompt = tc.get("system_prompt", "请将以下英文翻译为中文。")
        self.chunk_size = tc.get("chunk_size", 3000)

        # 音频配置
        ac = config.get("audio", {})
        self.voiceprint_start = ac.get("voiceprint_start", 60)
        self.voiceprint_duration = config.get("cosyvoice", {}).get("voiceprint_duration", 20)
        self.download_timeout = ac.get("download_timeout", 120)

        # 代理
        self.proxy = config.get("rss", {}).get("proxy")

    def run(
        self,
        audio_url: str,
        podcast_name: str = "unknown",
        episode_title: str = "",
        skip_steps: list[str] = None,
    ) -> PipelineContext:
        """
        执行完整工作流。

        Args:
            audio_url: 音频 MP3 的下载链接
            podcast_name: 播客名称
            episode_title: 节目标题
            skip_steps: 跳过的步骤列表，如 ["voiceprint", "tts"]

        Returns:
            PipelineContext 包含所有中间结果
        """
        skip = set(skip_steps or [])
        ctx = PipelineContext(
            podcast_name=podcast_name,
            episode_title=episode_title,
            audio_url=audio_url,
            start_time=time.time(),
        )

        print()
        print("=" * 65)
        print(f"  🚀 播客翻译工作流启动")
        print(f"  播客: {podcast_name}")
        print(f"  节目: {episode_title}")
        print(f"  Provider: STT={self.stt.name()} | LLM={self.llm.name()} | TTS={self.tts.name()}")
        print("=" * 65)

        try:
            # Step 1: 下载音频
            self._step(ctx, "download", self._download, ctx, skip)

            # Step 2: 提取声纹 & 上传 OSS
            if "voiceprint" not in skip:
                self._step(ctx, "voiceprint", self._extract_voiceprint, ctx, skip)

            # Step 3: 语音转文字
            self._step(ctx, "stt", self._transcribe, ctx, skip)

            # Step 4: 翻译
            self._step(ctx, "translate", self._translate, ctx, skip)

            # Step 5: TTS 合成
            if "tts" not in skip:
                self._step(ctx, "tts", self._synthesize, ctx, skip)

        except Exception as e:
            ctx.errors.append(f"Pipeline 异常: {e}")
            print(f"\n  ❌ 工作流异常: {e}")
            import traceback
            traceback.print_exc()

        # 总结
        total = time.time() - ctx.start_time
        print()
        print("=" * 65)
        print(f"  🏁 工作流完成！总耗时: {total:.1f}s")
        for step, t in ctx.step_times.items():
            print(f"     {step}: {t:.1f}s")
        if ctx.errors:
            print(f"  ⚠️ 错误: {len(ctx.errors)} 个")
            for err in ctx.errors:
                print(f"     - {err}")
        if ctx.final_audio_path:
            print(f"  📁 最终音频: {ctx.final_audio_path}")
        print("=" * 65)

        return ctx

    def _step(self, ctx, name, func, *args, **kwargs):
        """执行单步并记录耗时"""
        print(f"\n{'─' * 50}")
        print(f"  📌 Step: {name}")
        print(f"{'─' * 50}")
        t0 = time.time()
        try:
            func(*args, **kwargs)
        except Exception as e:
            ctx.errors.append(f"[{name}] {e}")
            raise
        finally:
            ctx.step_times[name] = time.time() - t0

    # ============================================================
    # 各步骤实现
    # ============================================================

    def _download(self, ctx: PipelineContext, skip: set):
        ctx.local_audio_path = download_audio(
            url=ctx.audio_url,
            output_dir=self.audio_dir,
            timeout=self.download_timeout,
            proxy=self.proxy,
        )

    def _extract_voiceprint(self, ctx: PipelineContext, skip: set):
        if not ctx.local_audio_path:
            raise ValueError("音频未下载")

        # 提取声纹片段
        ctx.voiceprint_local_path = extract_voiceprint(
            audio_path=ctx.local_audio_path,
            output_dir=self.voiceprint_dir,
            start_sec=self.voiceprint_start,
            duration_sec=self.voiceprint_duration,
        )

        # 上传到 OSS
        if self.storage:
            ctx.voiceprint_oss_url = self.storage.upload_voiceprint(
                ctx.voiceprint_local_path, ctx.podcast_name
            )
            print(f"  ✅ 声纹已上传到 OSS: {ctx.voiceprint_oss_url}")
        else:
            print("  ⚠️ 未配置 OSS，声纹不会上传（TTS 将使用默认音色）")
            ctx.voiceprint_oss_url = None  # 明确设置为 None

    def _transcribe(self, ctx: PipelineContext, skip: set):
        if not ctx.local_audio_path:
            raise ValueError("音频未下载")

        # 如果有 OSS，上传音频后用 URL 转写（支持大文件）
        if self.storage and hasattr(self.stt, "transcribe_with_oss"):
            audio_url = self.storage.upload(ctx.local_audio_path)
            ctx.transcript = self.stt.transcribe_with_oss(audio_url)
        elif self.storage and hasattr(self.stt, "transcribe_with_url"):
            audio_url = self.storage.upload(ctx.local_audio_path)
            ctx.transcript = self.stt.transcribe_with_url(audio_url)
        else:
            ctx.transcript = self.stt.transcribe(ctx.local_audio_path)

        # 保存转写文本
        os.makedirs(self.transcript_dir, exist_ok=True)
        safe_name = ctx.episode_title[:50] or "transcript"
        import re
        safe_name = re.sub(r'[^\w\-]', '_', safe_name)

        ctx.transcript_path = os.path.join(self.transcript_dir, f"{safe_name}.txt")
        with open(ctx.transcript_path, "w", encoding="utf-8") as f:
            f.write(ctx.transcript.to_timestamped_text())
        print(f"  💾 转写文本: {ctx.transcript_path}")

    def _translate(self, ctx: PipelineContext, skip: set):
        if not ctx.transcript:
            raise ValueError("转写结果为空")

        text = ctx.transcript.to_plain_text()

        # 分块翻译
        chunks = self._split_to_chunks(text, self.chunk_size)
        print(f"  📝 文本长度: {len(text)} 字，分为 {len(chunks)} 段翻译")

        ctx.translation = self.llm.translate_chunks(chunks, self.system_prompt)

        # 保存翻译
        os.makedirs(self.translation_dir, exist_ok=True)
        safe_name = ctx.episode_title[:50] or "translation"
        import re
        safe_name = re.sub(r'[^\w\-]', '_', safe_name)

        ctx.translation_path = os.path.join(self.translation_dir, f"{safe_name}_zh.txt")
        with open(ctx.translation_path, "w", encoding="utf-8") as f:
            f.write(ctx.translation.translated_text)
        print(f"  💾 翻译文本: {ctx.translation_path}")

    def _synthesize(self, ctx: PipelineContext, skip: set):
        if not ctx.translation:
            raise ValueError("翻译结果为空")

        os.makedirs(self.final_dir, exist_ok=True)
        safe_name = ctx.episode_title[:50] or "output"
        import re
        safe_name = re.sub(r'[^\w\-]', '_', safe_name)
        output_path = os.path.join(self.final_dir, f"{safe_name}_zh.mp3")

        # 使用长文本合成
        voice_url = ctx.voiceprint_oss_url or None

        if hasattr(self.tts, "synthesize_long"):
            ctx.tts_result = self.tts.synthesize_long(
                text=ctx.translation.translated_text,
                output_path=output_path,
                voice_url=voice_url,
            )
        else:
            ctx.tts_result = self.tts.synthesize(
                text=ctx.translation.translated_text,
                output_path=output_path,
                voice_url=voice_url,
            )

        ctx.final_audio_path = output_path

    @staticmethod
    def _split_to_chunks(text: str, max_size: int) -> list[str]:
        """按段落分块，每块不超过 max_size 字符"""
        paragraphs = text.split("\n\n")
        chunks = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) + 2 > max_size and current:
                chunks.append(current.strip())
                current = para
            else:
                current = current + "\n\n" + para if current else para

        if current.strip():
            chunks.append(current.strip())

        return chunks if chunks else [text]