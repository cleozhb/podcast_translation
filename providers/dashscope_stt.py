"""
providers/dashscope_stt.py
==========================
阿里云 DashScope Paraformer 语音识别
文档: https://help.aliyun.com/document_detail/2712536.html
"""

import json
import dashscope
from dashscope.audio.asr import Transcription
from providers.base import STTProvider, TranscriptResult, TranscriptSegment


class DashScopeSTT(STTProvider):
    """阿里云 DashScope Paraformer 语音转文字"""

    def __init__(self, config: dict):
        super().__init__(config)
        dc = config.get("dashscope", {})
        dashscope.api_key = dc.get("api_key", "")
        self.model = dc.get("stt_model", "paraformer-v2")

    def transcribe(self, audio_path: str, language: str = "en") -> TranscriptResult:
        print(f"  🎤 [DashScope STT] 正在转写: {audio_path}")
        print(f"     模型: {self.model}")

        # DashScope Paraformer 支持本地文件
        # 对于大文件，需要先上传或使用文件 URL
        response = Transcription.call(
            model=self.model,
            file_urls=[audio_path] if audio_path.startswith("http") else None,
            language_hints=[language],
        )

        # 如果是本地文件，使用异步转写
        if not audio_path.startswith("http"):
            response = Transcription.async_call(
                model=self.model,
                file_urls=[],  # 本地文件需要先上传
                language_hints=[language],
            )
            # 注意：实际使用时本地文件需要先上传到 OSS
            # 这里给出框架，具体实现需要配合 oss_storage

        result = TranscriptResult(language=language)

        if response.status_code == 200:
            # 解析转写结果
            transcripts = response.output.get("results", [])
            for t in transcripts:
                sentences = t.get("transcription_url", "")
                # Paraformer 返回的结构可能因版本不同而不同
                # 这里做通用解析
                if "sentences" in t:
                    for sent in t["sentences"]:
                        seg = TranscriptSegment(
                            start=sent.get("begin_time", 0) / 1000,
                            end=sent.get("end_time", 0) / 1000,
                            text=sent.get("text", ""),
                        )
                        result.segments.append(seg)

            result.full_text = " ".join(seg.text for seg in result.segments)
            print(f"  ✅ 转写完成，共 {len(result.segments)} 个片段")
        else:
            raise RuntimeError(
                f"DashScope STT 失败: {response.status_code} - {response.message}"
            )

        return result

    def transcribe_with_oss(
        self, audio_url: str, language: str = "en"
    ) -> TranscriptResult:
        """
        使用 OSS 公网 URL 进行转写（推荐方式，支持大文件）。

        Args:
            audio_url: 音频文件的公网 URL
            language: 语言代码
        """
        print(f"  🎤 [DashScope STT] 正在转写 URL: {audio_url[:80]}...")

        response = Transcription.async_call(
            model=self.model,
            file_urls=[audio_url],
            language_hints=[language],
        )

        # 等待异步任务完成
        import time
        task_id = response.output.get("task_id")
        print(f"     任务 ID: {task_id}，等待完成...")

        while True:
            status = Transcription.fetch(task=task_id)
            if status.output.get("task_status") == "SUCCEEDED":
                break
            elif status.output.get("task_status") == "FAILED":
                raise RuntimeError(f"转写任务失败: {status.output}")
            time.sleep(3)
            print("     等待中...")

        # 解析结果
        result = TranscriptResult(language=language)
        results = status.output.get("results", [])
        for r in results:
            url = r.get("transcription_url", "")
            if url:
                import requests
                resp = requests.get(url)
                data = resp.json()
                for sent in data.get("transcripts", [{}])[0].get("sentences", []):
                    seg = TranscriptSegment(
                        start=sent.get("begin_time", 0) / 1000,
                        end=sent.get("end_time", 0) / 1000,
                        text=sent.get("text", ""),
                    )
                    result.segments.append(seg)

        result.full_text = " ".join(seg.text for seg in result.segments)
        print(f"  ✅ 转写完成，共 {len(result.segments)} 个片段")
        return result