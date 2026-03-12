"""流水线总控：编排 RSS → ASR → 翻译 → TTS 完整流程。"""

from pathlib import Path

from loguru import logger

from asr.aliyun_asr import DashScopeASR
from asr.base import TranscriptResult
from config import Settings
from pipeline.progress_tracker import ProgressTracker
from rss.audio_downloader import download_audio
from rss.feed_parser import PodcastEpisode, fetch_episodes
from translator.qwen_translator import QwenTranslator
from tts.aliyun_tts import AliyunCosyVoiceTTS
from tts.voice_cloner import extract_voice_sample


class PodcastPipeline:
    """播客翻译流水线。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.data_dir = Path(settings.data_dir)
        self.tracker = ProgressTracker(str(self.data_dir / "progress.db"))

        # 初始化各模块 - 全部使用 DashScope API Key
        self.asr = DashScopeASR(
            api_key=settings.dashscope_api_key,
            model=settings.dashscope_asr_model,
        )
        self.translator = QwenTranslator(
            api_key=settings.dashscope_api_key,
            model=settings.llm_model,
        )
        self.tts = AliyunCosyVoiceTTS(
            api_key=settings.dashscope_api_key,
            model=settings.dashscope_tts_model,
        )

    def process_feed(self, feed_url: str, max_episodes: int | None = None):
        """处理一个 RSS feed 中的最新 N 个 episode。"""
        episodes = fetch_episodes(feed_url)
        limit = max_episodes or self.settings.max_episodes_per_feed

        logger.info(f"开始处理 feed: {episodes[0].podcast_name if episodes else 'unknown'}")
        processed = 0

        for episode in episodes[:limit]:
            if self.tracker.is_completed(episode.guid):
                logger.info(f"跳过已完成: {episode.title}")
                continue

            try:
                self.process_episode(episode)
                processed += 1
            except Exception as e:
                logger.error(f"处理失败: {episode.title} - {e}")
                self.tracker.mark_failed(episode.guid, str(e))

        logger.info(f"Feed 处理完成: 成功 {processed} 个 episode")

    def process_episode(self, episode: PodcastEpisode):
        """处理单个 episode 的完整流程。"""
        logger.info(f"开始处理: {episode.title}")
        guid = episode.guid

        self.tracker.update_status(
            guid, "downloading",
            podcast_name=episode.podcast_name,
            title=episode.title,
        )

        # Step 1: 下载音频
        audio_dir = self.data_dir / "audio_raw"
        audio_path = download_audio(episode.audio_url, guid, audio_dir)
        self.tracker.update_status(guid, "transcribing", audio_path=audio_path)

        # Step 2: ASR 转录
        # 优先使用播客原始 CDN URL（无需额外上传）
        transcript = self.asr.transcribe(episode.audio_url)

        # 保存英文转录
        transcript_dir = self.data_dir / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"{audio_path.stem}.txt"
        transcript_path.write_text(transcript.full_text, encoding="utf-8")
        self.tracker.update_status(
            guid, "translating", transcript_path=transcript_path
        )

        # Step 3: LLM 翻译
        translation = self.translator.translate(
            transcript.full_text,
            podcast_name=episode.podcast_name,
            episode_title=episode.title,
        )

        # 保存中文翻译
        translation_dir = self.data_dir / "translations"
        translation_dir.mkdir(parents=True, exist_ok=True)
        translation_path = translation_dir / f"{audio_path.stem}.txt"
        translation_path.write_text(translation, encoding="utf-8")
        self.tracker.update_status(
            guid, "synthesizing", translation_path=translation_path
        )

        # Step 4: 提取声纹 + TTS 合成
        voice_sample = extract_voice_sample(
            audio_path, transcript, episode.podcast_name
        )

        output_dir = self.data_dir / "audio_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{audio_path.stem}_zh.mp3"

        self.tts.synthesize(
            translation, output_path, voice_sample_path=voice_sample
        )
        self.tracker.update_status(
            guid, "completed", output_audio_path=output_path
        )

        logger.info(f"处理完成: {episode.title} → {output_path}")

    def process_local_file(
        self,
        audio_path: str | Path,
        podcast_name: str = "本地测试",
        episode_title: str = "本地音频测试",
    ):
        """
        使用本地已下载的 MP3 文件跑完整工作流（ASR → 翻译 → TTS）。

        跳过 RSS 解析和音频下载步骤，直接从本地文件开始。

        Args:
            audio_path: 本地 MP3 文件路径
            podcast_name: 播客名称（用于翻译上下文和声纹缓存）
            episode_title: 节目标题（用于翻译上下文）
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        guid = f"local_{audio_path.stem}"
        logger.info(f"开始本地文件测试: {audio_path.name}")

        # Step 1: ASR 转录（使用本地文件路径，需要 file:// 协议）
        self.tracker.update_status(
            guid, "transcribing",
            podcast_name=podcast_name,
            title=episode_title,
            audio_path=audio_path,
        )
        file_url = f"file://{audio_path.resolve()}"
        transcript = self.asr.transcribe(file_url)

        # 保存英文转录
        transcript_dir = self.data_dir / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"{audio_path.stem}.txt"
        transcript_path.write_text(transcript.full_text, encoding="utf-8")
        self.tracker.update_status(guid, "translating", transcript_path=transcript_path)
        logger.info(f"ASR 转录完成: {len(transcript.segments)} 个句子")

        # Step 2: LLM 翻译
        translation = self.translator.translate(
            transcript.full_text,
            podcast_name=podcast_name,
            episode_title=episode_title,
        )

        # 保存中文翻译
        translation_dir = self.data_dir / "translations"
        translation_dir.mkdir(parents=True, exist_ok=True)
        translation_path = translation_dir / f"{audio_path.stem}.txt"
        translation_path.write_text(translation, encoding="utf-8")
        self.tracker.update_status(guid, "synthesizing", translation_path=translation_path)
        logger.info("LLM 翻译完成")

        # Step 3: 提取声纹 + TTS 合成
        voice_sample = extract_voice_sample(audio_path, transcript, podcast_name)

        output_dir = self.data_dir / "audio_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{audio_path.stem}_zh.mp3"

        self.tts.synthesize(translation, output_path, voice_sample_path=voice_sample)
        self.tracker.update_status(guid, "completed", output_audio_path=output_path)

        logger.info(f"本地文件测试完成: {audio_path.name} → {output_path}")
