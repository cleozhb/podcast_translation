"""DashScope 语音识别（长音频异步模式）。"""

import dashscope
from loguru import logger

from asr.base import BaseASR, TranscriptResult, TranscriptSegment
from utils.retry import retry


class DashScopeASR(BaseASR):
    """
    DashScope 语音识别服务（使用 Fun-ASR / Qwen-ASR 模型）。

    支持长音频文件异步识别，返回带时间戳的转录结果。
    
    工作流程：
    1. 提交异步转录任务
    2. 等待任务完成
    3. 解析返回结果，提取带时间戳的文本
    """

    def __init__(self, api_key: str, model: str = "Fun-ASR"):
        """
        初始化 DashScope ASR。

        Args:
            api_key: DashScope API Key
            model: ASR 模型名称
                - Fun-ASR：最新一代，噪声鲁棒性更强（推荐）
                - qwen3-asr-flash：性价比高，快速经济
        """
        self.api_key = api_key
        self.model = model
        dashscope.api_key = self.api_key

    @retry(max_retries=3)
    def transcribe(self, audio_url: str) -> TranscriptResult:
        """
        对音频文件进行语音识别。

        Args:
            audio_url: 音频的公网可访问 URL（播客 CDN URL 或 OSS URL）

        Returns:
            TranscriptResult: 转录结果，包含完整文本和带时间戳的片段
        """
        logger.info(f"开始 DashScope ASR 识别，模型：{self.model}")
        logger.info(f"音频来源：{audio_url[:100]}...")

        try:
            # 提交异步转录任务
            task_response = dashscope.audio.asr.Transcription.async_call(
                model=self.model,
                file_urls=[audio_url]
            )

            if task_response.status_code != 200:
                raise RuntimeError(
                    f"ASR 任务提交失败：{task_response.status_code} - {task_response.message}"
                )

            task_id = task_response.output.task_id
            logger.info(f"ASR 任务已提交：{task_id}")

            # 等待任务完成
            transcription_response = dashscope.audio.asr.Transcription.wait(
                task=task_id
            )

            if transcription_response.status_code != 200:
                raise RuntimeError(
                    f"ASR 任务执行失败：{transcription_response.status_code} - {transcription_response.message}"
                )

            # 解析结果
            return self._parse_result(transcription_response)

        except Exception as e:
            logger.error(f"DashScope ASR 识别失败：{e}")
            raise

    def _parse_result(self, response) -> TranscriptResult:
        """解析 DashScope ASR 返回结果。"""
        segments = []
        texts = []

        # 从响应中提取句子级别的转录结果
        results = response.output.results
        
        for result in results:
            # 每个 result 对应一个音频文件的识别结果
            sentences = result.get('sentences', [])
            
            for sent in sentences:
                text = sent.get('text', '')
                if text.strip():
                    texts.append(text)
                    segments.append(
                        TranscriptSegment(
                            text=text,
                            start_ms=sent.get('begin_time', 0),
                            end_ms=sent.get('end_time', 0),
                        )
                    )

        full_text = " ".join(texts)
        logger.info(f"DashScope ASR 结果：{len(segments)} 个句子，{len(full_text)} 字符")
        
        # 检测语言（简单判断）
        language = "zh" if any('\u4e00' <= c <= '\u9fff' for c in full_text[:100]) else "en"
        
        return TranscriptResult(
            full_text=full_text,
            segments=segments,
            language=language
        )
