"""
core/pipeline.py
================
工作流编排引擎：串联 STT → LLM → TTS 各步骤
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from providers.base import (
    STTProvider, LLMProvider, TTSProvider, StorageProvider,
    TranscriptResult, TranslationResult, TTSResult, TranscriptSegment,
)
from core.audio_utils import download_audio, extract_voiceprints_auto, DiarizationResult, VoiceprintInfo, SpeakerSegment
from core.progress import ProgressTracker


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

    # Step 1.5: 音频公网 URL（原始 URL 或上传 OSS 后的 URL，避免重复上传）
    oss_audio_url: str = ""

    # Step 2: 声纹
    voiceprints: list = field(default_factory=list)          # list[VoiceprintInfo]
    diarization_result: Optional[DiarizationResult] = None   # 完整说话人时间线
    voiceprint_local_path: str = ""                          # 主持人声纹（兼容单人模式）
    voiceprint_oss_url: str = ""
    voiceprint_oss_urls: dict = field(default_factory=dict)  # {speaker_id: oss_url}

    # Step 3: STT（带说话人标签）
    transcript: Optional[TranscriptResult] = None
    transcript_path: str = ""

    # Step 4: 翻译（按说话人分段翻译）
    translation: Optional[TranslationResult] = None
    translation_path: str = ""
    # 带说话人标签的翻译段落: [{speaker, original, translated}, ...]
    speaker_translations: list = field(default_factory=list)

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
        progress: Optional[ProgressTracker] = None,
    ):
        self.config = config
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.storage = storage
        self.progress = progress

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
        self.download_timeout = ac.get("download_timeout", 120)

        # 说话人分离配置
        dc = config.get("diarization", {})
        self.diarization_method = dc.get("method", "energy")
        self.pyannote_config = dc.get("pyannote", {})
        vp_config = dc.get("voiceprint", {})
        self.voiceprint_duration = vp_config.get("target_duration", 20)

        # dashscope 说话人分离的模型配置（复用 dashscope.stt_model，可单独覆盖）
        dashscope_cfg = config.get("dashscope", {})
        self.diarization_model = dc.get("model", dashscope_cfg.get("stt_model", "paraformer-v2"))
        self.diarization_language_hints = dc.get("language_hints", ["en"])

        # 代理
        self.proxy = config.get("rss", {}).get("proxy")

    def run(
        self,
        audio_url: str = "",
        podcast_name: str = "unknown",
        episode_title: str = "",
        skip_steps: list[str] = None,
        local_audio_path: str = "",
    ) -> PipelineContext:
        """
        执行完整工作流。

        Args:
            audio_url: 音频 MP3 的下载链接
            podcast_name: 播客名称
            episode_title: 节目标题
            skip_steps: 跳过的步骤列表，如 ["voiceprint", "tts"]
            local_audio_path: 本地音频文件路径，如果指定则跳过下载步骤

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
        if local_audio_path:
            ctx.local_audio_path = local_audio_path

        # 断点续跑：加载已完成步骤并恢复上下文
        self._episode_id = None
        self._completed_steps: dict[str, dict] = {}
        if self.progress:
            self._episode_id = self.progress.get_or_create_episode(
                audio_url, podcast_name, episode_title
            )
            self._completed_steps = self.progress.get_completed_steps(self._episode_id)
            if self._completed_steps:
                self._restore_context(ctx, self._completed_steps)
                print(f"  📋 已完成步骤: {', '.join(self._completed_steps.keys())}")

        print()
        print("=" * 65)
        print(f"  🚀 播客翻译工作流启动")
        print(f"  播客: {podcast_name}")
        print(f"  节目: {episode_title}")
        print(f"  Provider: STT={self.stt.name()} | LLM={self.llm.name()} | TTS={self.tts.name()}")
        print("=" * 65)

        try:
            # Step 1: 下载音频
            self._run_step(ctx, "download", self._download, skip)

            # Step 2: 提取声纹 & 上传 OSS
            if "voiceprint" not in skip:
                self._run_step(ctx, "voiceprint", self._extract_voiceprint, skip)
            elif self.progress and self._episode_id:
                self.progress.mark_step_skipped(self._episode_id, "voiceprint")

            # Step 3: 语音转文字
            self._run_step(ctx, "stt", self._transcribe, skip)

            # Step 4: 翻译
            self._run_step(ctx, "translate", self._translate, skip)

            # Step 5: TTS 合成
            if "tts" not in skip:
                self._run_step(ctx, "tts", self._synthesize, skip)
            elif self.progress and self._episode_id:
                self.progress.mark_step_skipped(self._episode_id, "tts")

        except Exception as e:
            ctx.errors.append(f"Pipeline 异常: {e}")
            print(f"\n  ❌ 工作流异常: {e}")
            import traceback
            traceback.print_exc()
            if self.progress and self._episode_id:
                self.progress.mark_episode_failed(self._episode_id, str(e))
        else:
            if self.progress and self._episode_id:
                self.progress.mark_episode_completed(self._episode_id)

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

    def _run_step(self, ctx, name, func, skip):
        """执行单步：已完成则跳过，否则执行并记录进度。"""
        # 断点续跑：跳过已完成的步骤
        if name in self._completed_steps:
            print(f"\n{'─' * 50}")
            print(f"  ⏭️  Step: {name} (已完成，跳过)")
            print(f"{'─' * 50}")
            return

        print(f"\n{'─' * 50}")
        print(f"  📌 Step: {name}")
        print(f"{'─' * 50}")
        t0 = time.time()
        try:
            func(ctx, skip)
        except Exception as e:
            ctx.errors.append(f"[{name}] {e}")
            if self.progress and self._episode_id:
                self.progress.mark_step_failed(self._episode_id, name, str(e))
            raise
        finally:
            ctx.step_times[name] = time.time() - t0

        # 成功：持久化该步骤结果
        if self.progress and self._episode_id:
            result_data = self._extract_step_result(ctx, name)
            self.progress.mark_step_completed(self._episode_id, name, result_data)

    # ============================================================
    # 断点续跑：提取 / 恢复步骤结果
    # ============================================================

    @staticmethod
    def _extract_step_result(ctx: 'PipelineContext', step_name: str) -> dict:
        """从 PipelineContext 提取该步骤要持久化的数据。"""
        if step_name == "download":
            return {"local_audio_path": ctx.local_audio_path}
        elif step_name == "voiceprint":
            # 序列化 voiceprints
            voiceprints_data = [
                {
                    "speaker": vp.speaker,
                    "audio_path": vp.audio_path,
                    "duration": vp.duration,
                    "source_start": vp.source_start,
                    "source_end": vp.source_end,
                    "is_host": vp.is_host,
                }
                for vp in ctx.voiceprints
            ]
            # 序列化 diarization_result
            diarization_data = None
            if ctx.diarization_result:
                diarization_data = {
                    "num_speakers": ctx.diarization_result.num_speakers,
                    "speaker_durations": ctx.diarization_result.speaker_durations,
                    "segments": [
                        {"speaker": s.speaker, "start": s.start, "end": s.end}
                        for s in ctx.diarization_result.segments
                    ],
                }
            return {
                "voiceprint_local_path": ctx.voiceprint_local_path,
                "voiceprint_oss_url": ctx.voiceprint_oss_url,
                "voiceprint_oss_urls": ctx.voiceprint_oss_urls,
                "voiceprints": voiceprints_data,
                "diarization_result": diarization_data,
            }
        elif step_name == "stt":
            json_path = ctx.transcript_path.replace(".txt", ".json") if ctx.transcript_path else ""
            return {
                "transcript_path": ctx.transcript_path,
                "transcript_json_path": json_path,
            }
        elif step_name == "translate":
            return {
                "translation_path": ctx.translation_path,
                "speaker_translations": ctx.speaker_translations,
            }
        elif step_name == "tts":
            return {"final_audio_path": ctx.final_audio_path}
        return {}

    def _restore_context(self, ctx: 'PipelineContext', completed_steps: dict) -> None:
        """从已保存的步骤结果恢复 PipelineContext 字段。

        如果某步依赖的文件已缺失，则该步及后续步骤视为未完成。
        """
        ordered = [s for s in ProgressTracker.STEPS if s in completed_steps]

        for step in ordered:
            data = completed_steps[step]

            if step == "download":
                path = data.get("local_audio_path", "")
                if path and os.path.exists(path):
                    ctx.local_audio_path = path
                else:
                    self._invalidate_from(step, completed_steps)
                    return

            elif step == "voiceprint":
                ctx.voiceprint_local_path = data.get("voiceprint_local_path", "")
                ctx.voiceprint_oss_url = data.get("voiceprint_oss_url", "")
                ctx.voiceprint_oss_urls = data.get("voiceprint_oss_urls", {})
                # 恢复 voiceprints
                for vp_data in data.get("voiceprints", []):
                    ctx.voiceprints.append(VoiceprintInfo(
                        speaker=vp_data["speaker"],
                        audio_path=vp_data["audio_path"],
                        duration=vp_data["duration"],
                        source_start=vp_data["source_start"],
                        source_end=vp_data["source_end"],
                        is_host=vp_data.get("is_host", False),
                    ))
                # 恢复 diarization_result
                dr_data = data.get("diarization_result")
                if dr_data:
                    ctx.diarization_result = DiarizationResult(
                        num_speakers=dr_data["num_speakers"],
                        speaker_durations=dr_data.get("speaker_durations", {}),
                        segments=[
                            SpeakerSegment(
                                speaker=s["speaker"], start=s["start"], end=s["end"],
                            )
                            for s in dr_data.get("segments", [])
                        ],
                    )

            elif step == "stt":
                json_path = data.get("transcript_json_path", "")
                txt_path = data.get("transcript_path", "")
                if json_path and os.path.exists(json_path):
                    ctx.transcript = self._load_transcript_from_json(json_path)
                    ctx.transcript_path = txt_path
                elif txt_path and os.path.exists(txt_path):
                    ctx.transcript = self._load_transcript_from_txt(txt_path)
                    ctx.transcript_path = txt_path
                else:
                    self._invalidate_from(step, completed_steps)
                    return

            elif step == "translate":
                ctx.speaker_translations = data.get("speaker_translations", [])
                t_path = data.get("translation_path", "")
                if t_path and os.path.exists(t_path):
                    ctx.translation_path = t_path
                    with open(t_path, "r", encoding="utf-8") as f:
                        text = f.read()
                    ctx.translation = TranslationResult(translated_text=text)
                else:
                    self._invalidate_from(step, completed_steps)
                    return

            elif step == "tts":
                ctx.final_audio_path = data.get("final_audio_path", "")

    def _invalidate_from(self, step_name: str, completed_steps: dict) -> None:
        """文件缺失时，将该步骤及后续步骤从已完成集合中移除。"""
        found = False
        for s in ProgressTracker.STEPS:
            if s == step_name:
                found = True
            if found:
                completed_steps.pop(s, None)
        print(f"  ⚠️ 步骤 {step_name} 的输出文件缺失，将从此步骤开始重跑")

    @staticmethod
    def _load_transcript_from_json(json_path: str) -> TranscriptResult:
        """从 JSON sidecar 恢复 TranscriptResult。"""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = [
            TranscriptSegment(
                start=s["start"], end=s["end"], text=s["text"],
                speaker=s.get("speaker", ""),
            )
            for s in data.get("segments", [])
        ]
        return TranscriptResult(
            segments=segments,
            full_text=data.get("full_text", ""),
            language=data.get("language", "en"),
            duration=data.get("duration", 0.0),
        )

    @staticmethod
    def _load_transcript_from_txt(txt_path: str) -> TranscriptResult:
        """从纯文本文件回退恢复（无说话人信息）。"""
        with open(txt_path, "r", encoding="utf-8") as f:
            text = f.read()
        return TranscriptResult(full_text=text)

    # ============================================================
    # 各步骤实现
    # ============================================================

    def _download(self, ctx: PipelineContext, skip: set):
        if ctx.local_audio_path:
            print(f"  ... 使用本地文件: {ctx.local_audio_path}")
            return
        ctx.local_audio_path = download_audio(
            url=ctx.audio_url,
            output_dir=self.audio_dir,
            timeout=self.download_timeout,
            proxy=self.proxy,
        )

    def _get_audio_url(self, ctx: PipelineContext, force_oss: bool = False) -> str:
        """获取音频的公网 URL，优先复用原始 URL，需要上传时只上传一次。
        
        Args:
            ctx: 上下文
            force_oss: 是否强制上传到 OSS（用于 DashScope 等无法直接访问外部 URL 的场景）
        """
        # 已经上传过 OSS，直接复用
        if ctx.oss_audio_url:
            return ctx.oss_audio_url

        # 强制上传到 OSS（用于 DashScope 等服务）
        if force_oss and self.storage and ctx.local_audio_path:
            print(f"  📤 上传音频到 OSS（DashScope 需要）...")
            ctx.oss_audio_url = self.storage.upload(ctx.local_audio_path)
            return ctx.oss_audio_url

        # 原始 URL 本身就是公网可访问的，直接用（排除本地文件伪 URL）
        if ctx.audio_url and ctx.audio_url.startswith(("http://", "https://")):
            print(f"  🔗 复用原始音频 URL（无需上传 OSS）")
            ctx.oss_audio_url = ctx.audio_url
            return ctx.oss_audio_url

        # fallback: 上传本地文件到 OSS
        if self.storage and ctx.local_audio_path:
            ctx.oss_audio_url = self.storage.upload(ctx.local_audio_path)
            return ctx.oss_audio_url

        raise ValueError("无法获取音频公网 URL：无原始 URL 且未配置 OSS")

    def _extract_voiceprint(self, ctx: PipelineContext, skip: set):
        if not ctx.local_audio_path:
            raise ValueError("音频未下载")

        # 如果用 dashscope 分离，需要公网可访问的音频 URL
        audio_url = None
        dashscope_key = None
        if self.diarization_method == "dashscope":
            audio_url = self._get_audio_url(ctx, force_oss=True)
            dashscope_key = self.config.get("dashscope", {}).get("api_key")

        # 一键：说话人分离 + 声纹提取
        voiceprints, diarization_result = extract_voiceprints_auto(
            audio_path=ctx.local_audio_path,
            output_dir=self.voiceprint_dir,
            method=self.diarization_method,
            target_duration=self.voiceprint_duration,
            hf_token=self.pyannote_config.get("hf_token"),
            num_speakers=self.pyannote_config.get("num_speakers"),
            audio_url=audio_url,
            dashscope_api_key=dashscope_key,
            dashscope_model=self.diarization_model,
            language_hints=self.diarization_language_hints,
        )

        if not voiceprints:
            print("  ⚠️ 未能提取声纹，TTS 将使用默认音色")
            return

        ctx.voiceprints = voiceprints
        ctx.diarization_result = diarization_result

        # 上传所有说话人的声纹到 OSS
        if self.storage:
            for vp in voiceprints:
                oss_url = self.storage.upload_voiceprint(
                    vp.audio_path,
                    f"{ctx.podcast_name}_{vp.speaker}",
                )
                ctx.voiceprint_oss_urls[vp.speaker] = oss_url
                if vp.is_host:
                    ctx.voiceprint_oss_url = oss_url
                    ctx.voiceprint_local_path = vp.audio_path

            print(f"  ☁️ 已上传 {len(ctx.voiceprint_oss_urls)} 个声纹到 OSS")
            for spk, url in ctx.voiceprint_oss_urls.items():
                role = "主持人" if any(vp.speaker == spk and vp.is_host for vp in voiceprints) else "嘉宾"
                print(f"     {spk} [{role}]: {url[:]}...")
        else:
            # 没有 OSS，只用主持人声纹
            host_vp = next((vp for vp in voiceprints if vp.is_host), voiceprints[0])
            ctx.voiceprint_local_path = host_vp.audio_path
            print("  ⚠️ 未配置 OSS，声纹不会上传（TTS 将使用默认音色）")

    def _transcribe(self, ctx: PipelineContext, skip: set):
        if not ctx.local_audio_path:
            raise ValueError("音频未下载")

        # 优先用公网 URL 转写（支持大文件），复用已有 URL 避免重复上传
        if hasattr(self.stt, "transcribe_with_oss"):
            audio_url = self._get_audio_url(ctx)
            ctx.transcript = self.stt.transcribe_with_oss(audio_url)
        elif hasattr(self.stt, "transcribe_with_url"):
            audio_url = self._get_audio_url(ctx)
            ctx.transcript = self.stt.transcribe_with_url(audio_url)
        else:
            ctx.transcript = self.stt.transcribe(ctx.local_audio_path)

        # 如果 STT 已返回说话人标签（如 DashScope），直接使用；否则用分离结果对齐
        if self._stt_provides_speaker_labels(ctx):
            print("  🏷️  STT 已提供说话人标签，跳过后处理对齐")
        elif ctx.voiceprints and self.diarization_method != "energy":
            self._align_diarization_to_transcript(ctx)

        # 保存转写文本
        os.makedirs(self.transcript_dir, exist_ok=True)
        safe_name = ctx.episode_title[:50] or "transcript"
        import re
        safe_name = re.sub(r'[^\w\-]', '_', safe_name)

        ctx.transcript_path = os.path.join(self.transcript_dir, f"{safe_name}.txt")
        with open(ctx.transcript_path, "w", encoding="utf-8") as f:
            f.write(ctx.transcript.to_timestamped_text())

        # 保存 JSON sidecar（供断点续跑恢复完整结构）
        json_path = ctx.transcript_path.replace(".txt", ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker}
                    for s in ctx.transcript.segments
                ],
                "full_text": ctx.transcript.full_text,
                "language": ctx.transcript.language,
                "duration": ctx.transcript.duration,
            }, f, ensure_ascii=False, indent=2)

        print(f"  💾 转写文本: {ctx.transcript_path}")

    def _translate(self, ctx: PipelineContext, skip: set):
        if not ctx.transcript:
            raise ValueError("转写结果为空")

        has_speakers = any(seg.speaker for seg in ctx.transcript.segments)

        if has_speakers and len(ctx.voiceprint_oss_urls) > 1:
            # 多说话人模式：按说话人分段翻译，保留标签
            self._translate_multi_speaker(ctx)
        else:
            # 单人模式：整段翻译
            self._translate_single(ctx)

        # 保存翻译
        os.makedirs(self.translation_dir, exist_ok=True)
        safe_name = ctx.episode_title[:50] or "translation"
        import re
        safe_name = re.sub(r'[^\w\-]', '_', safe_name)

        ctx.translation_path = os.path.join(self.translation_dir, f"{safe_name}_zh.txt")
        with open(ctx.translation_path, "w", encoding="utf-8") as f:
            if ctx.speaker_translations:
                for item in ctx.speaker_translations:
                    f.write(f"[{item['speaker']}] {item['translated']}\n\n")
            else:
                f.write(ctx.translation.translated_text)
        print(f"  💾 翻译文本: {ctx.translation_path}")

    def _translate_single(self, ctx: PipelineContext):
        """单人模式：全文翻译"""
        text = ctx.transcript.to_plain_text()
        chunks = self._split_to_chunks(text, self.chunk_size)
        print(f"  📝 单人模式：{len(text)} 字，分为 {len(chunks)} 段翻译")
        ctx.translation = self.llm.translate_chunks(chunks, self.system_prompt)

    def _translate_multi_speaker(self, ctx: PipelineContext):
        """多说话人模式：按说话人分段，批量翻译，保留标签"""
        # 将连续同一说话人的片段合并
        merged = self._merge_speaker_segments(ctx.transcript.segments)
        print(f"  📝 多说话人模式：{len(merged)} 段对话")

        # 构造带说话人标签的翻译输入
        # 批量翻译（按 chunk_size 分批）
        ctx.speaker_translations = []
        batch_text = ""
        batch_items = []

        for item in merged:
            line = f"[{item['speaker']}]: {item['text']}"
            if len(batch_text) + len(line) > self.chunk_size and batch_items:
                # 翻译这一批
                translated = self._translate_speaker_batch(batch_text, batch_items)
                ctx.speaker_translations.extend(translated)
                batch_text = ""
                batch_items = []

            batch_text += line + "\n\n"
            batch_items.append(item)

        # 最后一批
        if batch_items:
            translated = self._translate_speaker_batch(batch_text, batch_items)
            ctx.speaker_translations.extend(translated)

        # 也生成完整翻译文本
        full_translated = "\n\n".join(
            f"[{t['speaker']}] {t['translated']}" for t in ctx.speaker_translations
        )
        ctx.translation = TranslationResult(
            source_text="\n\n".join(m["text"] for m in merged),
            translated_text=full_translated,
        )

        print(f"  ✅ 多说话人翻译完成，共 {len(ctx.speaker_translations)} 段")

    def _translate_speaker_batch(self, batch_text: str, batch_items: list) -> list:
        """翻译一批带说话人标签的文本，支持重试和逐段 fallback"""
        import re
        max_retries = 1

        for attempt in range(max_retries + 1):
            is_retry = attempt > 0
            prompt = self._build_speaker_translation_prompt(
                is_retry=is_retry, expected_count=len(batch_items),
            )
            result = self.llm.translate(batch_text, prompt)
            translated_text = result.translated_text

            parsed = self._parse_speaker_translation(translated_text, len(batch_items))
            if parsed is not None:
                return [
                    {
                        "speaker": item["speaker"],
                        "original": item["text"],
                        "translated": p[1].strip(),
                        "start": item["start"],
                        "end": item["end"],
                    }
                    for item, p in zip(batch_items, parsed)
                ]

            if attempt < max_retries:
                print(f"  ⚠️ 翻译解析失败（尝试 {attempt + 1}/{max_retries + 1}），重试...")

        # 重试耗尽，逐段翻译兜底
        print(f"  ⚠️ 批量翻译解析失败，回退到逐段翻译（{len(batch_items)} 段）")
        return self._translate_segment_by_segment(batch_items)

    def _build_speaker_translation_prompt(self, is_retry: bool = False, expected_count: int = 0) -> str:
        """构造带说话人标签的翻译 prompt"""
        base = self.system_prompt + (
            "\n\n注意：文本中包含说话人标签如 [SPEAKER_00]:，"
            "请保留标签格式，只翻译冒号后面的内容。"
        )
        if is_retry:
            base += (
                f"\n\n【重要】请严格遵守以下格式要求："
                f"\n1. 输入共 {expected_count} 段，输出也必须恰好 {expected_count} 段"
                f"\n2. 每段必须以 [SPEAKER_XX]: 开头（保留原始标签）"
                f"\n3. 每段之间用空行分隔"
                f"\n4. 不要合并或拆分段落"
            )
        return base

    @staticmethod
    def _parse_speaker_translation(translated_text: str, expected_count: int):
        """
        解析带说话人标签的翻译结果。

        Returns:
            成功返回 list[(speaker, text)]，失败返回 None
        """
        import re

        # 主正则
        parsed = re.findall(
            r'\[(\w+)\][:\s]*(.*?)(?=\n*\[|\Z)',
            translated_text,
            re.DOTALL,
        )
        if len(parsed) == expected_count:
            return parsed

        # 兼容全角括号和全角冒号
        parsed = re.findall(
            r'[\[【](\w+)[\]】][:\s：]*(.*?)(?=\n*[\[【]|\Z)',
            translated_text,
            re.DOTALL,
        )
        if len(parsed) == expected_count:
            return parsed

        # 按空行分割兜底
        lines = [l.strip() for l in translated_text.split("\n\n") if l.strip()]
        if len(lines) == expected_count:
            results = []
            for line in lines:
                m = re.match(r'[\[【]?(\w+)[\]】]?[:\s：]*(.*)', line, re.DOTALL)
                if m:
                    results.append((m.group(1), m.group(2).strip()))
                else:
                    results.append(("", line))
            return results

        return None

    def _translate_segment_by_segment(self, batch_items: list) -> list:
        """逐段翻译 fallback：每段独立翻译，不依赖格式解析"""
        import re
        results = []
        for i, item in enumerate(batch_items):
            print(f"    逐段翻译 {i + 1}/{len(batch_items)}...")
            result = self.llm.translate(item["text"], self.system_prompt)
            translated = result.translated_text.strip()
            translated = re.sub(r'^[\[【]?\w+[\]】]?[:\s：]*', '', translated)
            results.append({
                "speaker": item["speaker"],
                "original": item["text"],
                "translated": translated,
                "start": item["start"],
                "end": item["end"],
            })
        return results

    @staticmethod
    def _merge_speaker_segments(segments) -> list[dict]:
        """将连续同一说话人的片段合并"""
        if not segments:
            return []

        merged = []
        current = {
            "speaker": segments[0].speaker or "SPEAKER_00",
            "text": segments[0].text,
            "start": segments[0].start,
            "end": segments[0].end,
        }

        for seg in segments[1:]:
            spk = seg.speaker or "SPEAKER_00"
            if spk == current["speaker"]:
                current["text"] += " " + seg.text
                current["end"] = seg.end
            else:
                merged.append(current)
                current = {
                    "speaker": spk,
                    "text": seg.text,
                    "start": seg.start,
                    "end": seg.end,
                }

        merged.append(current)
        return merged

    def _synthesize(self, ctx: PipelineContext, skip: set):
        if not ctx.translation:
            raise ValueError("翻译结果为空")

        os.makedirs(self.final_dir, exist_ok=True)
        safe_name = ctx.episode_title[:50] or "output"
        import re
        safe_name = re.sub(r'[^\w\-]', '_', safe_name)
        output_path = os.path.join(self.final_dir, f"{safe_name}_zh.mp3")

        has_multi_voice = (
            ctx.speaker_translations
            and len(ctx.voiceprint_oss_urls) > 1
        )

        if has_multi_voice:
            self._synthesize_multi_speaker(ctx, output_path)
        else:
            self._synthesize_single(ctx, output_path)

        ctx.final_audio_path = output_path

    def _synthesize_single(self, ctx: PipelineContext, output_path: str):
        """单音色合成"""
        voice_url = ctx.voiceprint_oss_url or None
        print(f"  🔊 单音色合成模式")

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

    def _synthesize_multi_speaker(self, ctx: PipelineContext, output_path: str):
        """
        多音色合成：按说话人分段合成，每段用对应的声纹，最后拼接。
        """
        from pydub import AudioSegment as PydubSegment
        import tempfile

        print(f"  🔊 多音色合成模式: {len(ctx.voiceprint_oss_urls)} 个声纹")
        for spk, url in ctx.voiceprint_oss_urls.items():
            role = "主持人" if any(
                vp.speaker == spk and vp.is_host for vp in ctx.voiceprints
            ) else "嘉宾"
            print(f"     {spk} [{role}]: {url[:60]}...")

        # 预创建所有说话人的音色（避免重复创建）
        if hasattr(self.tts, 'preload_voices'):
            unique_urls = list(set(ctx.voiceprint_oss_urls.values()))
            self.tts.preload_voices(unique_urls)

        combined = PydubSegment.empty()
        temp_files = []
        total = len(ctx.speaker_translations)

        try:
            for i, item in enumerate(ctx.speaker_translations, 1):
                speaker = item["speaker"]
                text = item["translated"]

                if not text.strip():
                    continue

                # 选择声纹：优先用该说话人的，fallback 到主持人的
                voice_url = ctx.voiceprint_oss_urls.get(
                    speaker,
                    ctx.voiceprint_oss_url,
                )

                role = "主持人" if any(
                    vp.speaker == speaker and vp.is_host for vp in ctx.voiceprints
                ) else "嘉宾"
                print(f"     [{i:3d}/{total}] {speaker} [{role}] "
                      f"({len(text)}字): {text[:30]}...")

                temp_path = tempfile.mktemp(suffix=".mp3")
                temp_files.append(temp_path)

                # 合成单段（长文本自动分段，避免超出 API 限制导致音频异常）
                from providers.cosyvoice_tts import CosyVoiceTTS
                if isinstance(self.tts, CosyVoiceTTS) and len(text) > 300:
                    self.tts.synthesize_long(
                        text=text,
                        output_path=temp_path,
                        voice_url=voice_url,
                        max_chars=300,
                    )
                else:
                    self.tts.synthesize(
                        text=text,
                        output_path=temp_path,
                        voice_url=voice_url,
                    )

                segment = PydubSegment.from_file(temp_path)
                combined += segment

                # 说话人切换时加短暂停顿（更自然）
                if i < total:
                    next_speaker = ctx.speaker_translations[i]["speaker"]
                    if next_speaker != speaker:
                        combined += PydubSegment.silent(duration=500)  # 0.5s 停顿

            # 导出最终音频
            combined.export(output_path, format="mp3")
            duration = len(combined) / 1000

            ctx.tts_result = TTSResult(audio_path=output_path, duration=duration)
            print(f"  ✅ 多音色合成完成: {output_path} ({duration:.1f}s)")

        finally:
            for f in temp_files:
                if os.path.exists(f):
                    os.remove(f)

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

    @staticmethod
    def _stt_provides_speaker_labels(ctx: 'PipelineContext') -> bool:
        """检查 STT 结果是否已包含有效的说话人标签（如 DashScope 自带说话人分离）。"""
        if not ctx.transcript or not ctx.transcript.segments:
            return False
        speakers = set(seg.speaker for seg in ctx.transcript.segments if seg.speaker)
        labeled = sum(1 for seg in ctx.transcript.segments if seg.speaker)
        ratio = labeled / len(ctx.transcript.segments)
        return len(speakers) >= 2 and ratio > 0.8

    def _align_diarization_to_transcript(self, ctx: PipelineContext):
        """
        将说话人分离的时间戳与 STT 转写文本对齐。

        优先使用完整的 DiarizationResult 时间线（覆盖全部音频），
        若不可用（如断点续跑），fallback 到 voiceprint 的 source_start/end。
        """
        if not ctx.transcript:
            return

        # 优先用完整说话人时间线，fallback 到 voiceprint 的片段
        if ctx.diarization_result and ctx.diarization_result.segments:
            speaker_segments = [
                {'speaker': s.speaker, 'start': s.start, 'end': s.end}
                for s in ctx.diarization_result.segments
            ]
            print(f"  🏷️  使用完整说话人时间线对齐（{len(speaker_segments)} 个片段）")
        elif ctx.voiceprints:
            speaker_segments = [
                {'speaker': vp.speaker, 'start': vp.source_start, 'end': vp.source_end}
                for vp in ctx.voiceprints
            ]
            print(f"  🏷️  使用声纹片段对齐（fallback，仅 {len(speaker_segments)} 个片段）")
        else:
            return

        if not speaker_segments:
            return

        # 为每个转写片段分配说话人（基于时间重叠）
        for seg in ctx.transcript.segments:
            best_speaker = None
            best_overlap = 0

            for spk_seg in speaker_segments:
                overlap_start = max(seg.start, spk_seg['start'])
                overlap_end = min(seg.end, spk_seg['end'])
                overlap = max(0, overlap_end - overlap_start)

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = spk_seg['speaker']

            if best_speaker and best_overlap > 0:
                seg.speaker = best_speaker
            else:
                # fallback: 找时间最近的说话人
                closest = min(
                    speaker_segments,
                    key=lambda s: abs(s['start'] - seg.start)
                )
                seg.speaker = closest['speaker']

        # 统计标注结果
        labeled_count = sum(1 for seg in ctx.transcript.segments if seg.speaker)
        print(f"  🏷️  已为 {labeled_count}/{len(ctx.transcript.segments)} 个片段标注说话人")
