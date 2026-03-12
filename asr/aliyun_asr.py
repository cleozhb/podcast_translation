"""阿里云录音文件识别（长音频异步模式）。"""

import json
import time

import requests
from loguru import logger

from asr.base import BaseASR, TranscriptResult, TranscriptSegment
from utils.retry import retry

# 阿里云 Token 获取 API
TOKEN_URL = "https://nls-meta.cn-shanghai.aliyuncs.com"
# 录音文件识别 API
FILE_TRANS_URL = "https://filetrans.cn-shanghai.aliyuncs.com"


class AliyunASR(BaseASR):
    """
    阿里云录音文件识别。

    工作流程：
    1. 使用 AccessKey 获取临时 Token
    2. 提交录音文件识别任务（传入音频公网 URL）
    3. 轮询任务状态，等待识别完成
    4. 解析返回结果，提取带时间戳的文本
    """

    def __init__(self, appkey: str, ak_id: str, ak_secret: str):
        self.appkey = appkey
        self.ak_id = ak_id
        self.ak_secret = ak_secret
        self._token = None
        self._token_expire = 0

    def _get_token(self) -> str:
        """获取阿里云 NLS 访问 Token。"""
        if self._token and time.time() < self._token_expire:
            return self._token

        params = {
            "Action": "CreateToken",
            "Version": "2019-02-28",
            "AccessKeyId": self.ak_id,
            "AccessKeySecret": self.ak_secret,
        }
        # 使用阿里云 SDK 或 REST API 获取 token
        # 这里使用简化的 REST 方式，生产环境建议用 alibabacloud SDK
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkcore.request import CommonRequest

        client = AcsClient(self.ak_id, self.ak_secret, "cn-shanghai")
        request = CommonRequest()
        request.set_method("POST")
        request.set_domain("nls-meta.cn-shanghai.aliyuncs.com")
        request.set_version("2019-02-28")
        request.set_action_name("CreateToken")

        response = json.loads(client.do_action_with_exception(request))
        self._token = response["Token"]["Id"]
        self._token_expire = response["Token"]["ExpireTime"]
        logger.info("阿里云 NLS Token 获取成功")
        return self._token

    @retry(max_retries=3)
    def transcribe(self, audio_url: str) -> TranscriptResult:
        """
        提交录音文件识别任务并等待结果。

        Args:
            audio_url: 音频的公网可访问 URL（播客 CDN URL 或 OSS URL）
        """
        token = self._get_token()

        # 提交识别任务
        task = {
            "appkey": self.appkey,
            "file_link": audio_url,
            "version": "4.0",
            "enable_words": False,
            "enable_sample_rate_adaptive": True,
            # 启用时间戳（用于后续声纹提取）
            "enable_timestamp": True,
            "auto_split": True,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        logger.info("提交 ASR 任务...")
        resp = requests.post(
            f"{FILE_TRANS_URL}/filetrans",
            json={"Task": json.dumps(task)},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("StatusText") != "SUCCESS":
            raise RuntimeError(f"ASR 任务提交失败: {result}")

        task_id = result["TaskId"]
        logger.info(f"ASR 任务已提交: {task_id}")

        # 轮询结果
        return self._poll_result(task_id, headers)

    def _poll_result(
        self, task_id: str, headers: dict, poll_interval: int = 10, max_wait: int = 3600
    ) -> TranscriptResult:
        """轮询 ASR 任务结果。"""
        elapsed = 0
        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            resp = requests.get(
                f"{FILE_TRANS_URL}/filetrans",
                params={"TaskId": task_id},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
            status = result.get("StatusText")

            if status == "SUCCESS":
                logger.info(f"ASR 识别完成 (耗时约 {elapsed}s)")
                return self._parse_result(result)
            elif status in ("RUNNING", "QUEUEING"):
                logger.debug(f"ASR 任务状态: {status}，已等待 {elapsed}s")
                continue
            else:
                raise RuntimeError(f"ASR 识别失败: {result}")

        raise TimeoutError(f"ASR 任务超时，已等待 {max_wait}s")

    def _parse_result(self, result: dict) -> TranscriptResult:
        """解析阿里云 ASR 返回结果。"""
        sentences = result.get("Result", {}).get("Sentences", [])

        segments = []
        texts = []
        for sent in sentences:
            text = sent.get("Text", "")
            texts.append(text)
            segments.append(
                TranscriptSegment(
                    text=text,
                    start_ms=sent.get("BeginTime", 0),
                    end_ms=sent.get("EndTime", 0),
                )
            )

        full_text = " ".join(texts)
        logger.info(f"ASR 结果: {len(segments)} 个句子, {len(full_text)} 字符")
        return TranscriptResult(full_text=full_text, segments=segments, language="en")
