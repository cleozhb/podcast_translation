"""流水线总控：编排 RSS → ASR → 翻译 → TTS 完整流程。"""

from pathlib import Path

from loguru import logger

from asr.aliyun_asr import AliyunASR
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

        # 初始化各模块
        self.asr = AliyunASR(
            appkey=settings.aliyun_asr_appkey,
            ak_id=settings.aliyun_access_key_id,
            ak_secret=settings.aliyun_access_key_secret,
        )
        self.translator = QwenTranslator(
            api_key=settings.dashscope_api_key,
            model=settings.llm_model,
        )
        self.tts = AliyunCosyVoiceTTS(
            api_key=settings.dashscope_api_key,
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
