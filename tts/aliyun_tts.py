"""阿里云 CosyVoice 音色克隆 TTS。"""

import base64
import json
import re
import time
from pathlib import Path

import requests
from loguru import logger

from tts.base import BaseTTS
from utils.retry import retry


def upload_to_oss(file_path: Path, oss_endpoint: str, oss_bucket: str, 
                  access_key_id: str, access_key_secret: str) -> str:
    """
    上传文件到阿里云 OSS，返回公网可访问的 URL。
    
    Args:
        file_path: 要上传的文件路径
        oss_endpoint: OSS Endpoint（如 oss-cn-beijing.aliyuncs.com）
        oss_bucket: OSS Bucket 名称
        access_key_id: 阿里云 AccessKey ID
        access_key_secret: 阿里云 AccessKey Secret
    
    Returns:
        文件的公网访问 URL
    """
    try:
        import oss2
        from oss2 import SizedFileAdapter, determine_part_size
        
        # 创建 Auth 对象
        auth = oss2.Auth(access_key_id, access_key_secret)
        
        # 创建 Bucket 对象
        bucket = oss2.Bucket(auth, oss_endpoint, oss_bucket)
        
        # 生成唯一的 OSS 对象键
        object_key = f"voice_samples/{file_path.stem}_{int(time.time())}.wav"
        
        # 上传文件
        logger.info(f"正在上传声纹样本到 OSS: {file_path.name} -> {object_key}")
        bucket.put_object_from_file(object_key, str(file_path))
        
        # 构建公网 URL
        public_url = f"https://{oss_bucket}.{oss_endpoint}/{object_key}"
        logger.info(f"OSS 上传成功，公网 URL: {public_url}")
        
        return public_url
        
    except ImportError:
        logger.error("未安装 oss2 库，请运行：pip install oss2")
        raise
    except Exception as e:
        logger.error(f"OSS 上传失败：{e}")
        raise


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
    def __init__(self, api_key: str, model: str = "cosyvoice-v3-plus"):
        """
        初始化 CosyVoice TTS。

        Args:
            api_key: DashScope API Key
            model: TTS 模型名称
                - cosyvoice-v3-plus：效果最佳，支持声音复刻（推荐）
                - cosyvoice-v3-flash：速度快，性价比高
                - cosyvoice-v2：成熟稳定，70+ 音色
        """
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
        from dashscope.audio.tts_v2 import SpeechSynthesizer, VoiceEnrollmentService
        
        dashscope.api_key = self.api_key

        # 确定使用的音色
        voice_to_use = "longxiaochun"  # 默认音色
        
        if voice_sample_path:
            # 声音克隆模式：先创建音色（异步任务）
            logger.info(f"正在从声纹样本创建音色...")
            
            # 检查是否配置了 OSS
            from config import load_settings
            settings = load_settings()
            
            if not (settings.oss_endpoint and settings.oss_bucket and 
                    settings.aliyun_access_key_id and settings.aliyun_access_key_secret):
                logger.warning("未配置 OSS 凭证，无法进行声音克隆，使用默认音色")
                logger.warning("如需声音克隆，请在 .env 中配置 OSS_ENDPOINT, OSS_BUCKET, ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET")
            else:
                try:
                    # 1. 上传声纹样本到 OSS
                    audio_url = upload_to_oss(
                        file_path=voice_sample_path,
                        oss_endpoint=settings.oss_endpoint,
                        oss_bucket=settings.oss_bucket,
                        access_key_id=settings.aliyun_access_key_id,
                        access_key_secret=settings.aliyun_access_key_secret,
                    )
                    
                    # 2. 创建音色（异步任务）
                    service = VoiceEnrollmentService()
                    voice_id = service.create_voice(
                        target_model=self.model,
                        prefix="podcast",
                        url=audio_url,
                    )
                    logger.info(f"音色创建任务已提交，voice_id: {voice_id}")
                    
                    # 3. 轮询等待音色就绪
                    max_attempts = 30
                    poll_interval = 10  # 秒
                    for attempt in range(max_attempts):
                        voice_info = service.query_voice(voice_id=voice_id)
                        status = voice_info.get("status")
                        
                        if status == "OK":
                            logger.info("音色已就绪")
                            voice_to_use = voice_id
                            break
                        elif status == "UNDEPLOYED":
                            logger.error(f"音色创建失败：{status}")
                            raise RuntimeError(f"音色创建失败：{status}")
                        else:
                            logger.debug(f"等待音色就绪 ({attempt + 1}/{max_attempts}): {status}")
                            time.sleep(poll_interval)
                    else:
                        logger.warning("音色创建超时，使用默认音色")
                        
                except Exception as e:
                    logger.warning(f"声音克隆流程失败，使用默认音色：{e}")
                    voice_to_use = "longxiaochun"

        # 构建合成参数
        synthesizer_params = {
            "model": self.model,
            "voice": voice_to_use,
        }

        # 创建合成器实例
        synthesizer = SpeechSynthesizer(**synthesizer_params)
        
        # 调用合成方法
        audio = synthesizer.call(text=text)

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
