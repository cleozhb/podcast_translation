"""
providers/baidu_stt.py
======================
百度千帆 语音识别（短音频 REST API + 长音频异步）
文档: https://ai.baidu.com/ai-doc/SPEECH/vlcghul0p
"""

import json
import time
import base64
import requests
from providers.base import STTProvider, TranscriptResult, TranscriptSegment


class BaiduSTT(STTProvider):
    """百度千帆语音识别"""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    # 短音频（<60s）
    SHORT_ASR_URL = "https://vop.baidu.com/server_api"
    # 长音频异步
    LONG_ASR_CREATE_URL = "https://aip.baidubce.com/rpc/2.0/aasr/v1/create"
    LONG_ASR_QUERY_URL = "https://aip.baidubce.com/rpc/2.0/aasr/v1/query"

    def __init__(self, config: dict):
        super().__init__(config)
        bc = config.get("baidu", {})
        self.api_key = bc.get("api_key", "")
        self.secret_key = bc.get("secret_key", "")
        self.app_id = bc.get("stt_app_id", "")
        self._token = None
        self._token_expires = 0

    def _get_token(self) -> str:
        """获取百度 API Access Token"""
        if self._token and time.time() < self._token_expires:
            return self._token

        resp = requests.post(self.TOKEN_URL, params={
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        })
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 2592000) - 60
        return self._token

    def transcribe(self, audio_path: str, language: str = "en") -> TranscriptResult:
        """
        自动判断音频时长，选择短音频或长音频接口。
        """
        print(f"  🎤 [百度 STT] 正在转写: {audio_path}")

        # 简单判断：文件大于 5MB 用长音频接口
        import os
        file_size = os.path.getsize(audio_path)

        if file_size < 5 * 1024 * 1024:
            return self._transcribe_short(audio_path, language)
        else:
            return self._transcribe_long(audio_path, language)

    def _transcribe_short(self, audio_path: str, language: str) -> TranscriptResult:
        """短音频识别（<60秒）"""
        print("     使用短音频接口...")
        token = self._get_token()

        with open(audio_path, "rb") as f:
            audio_data = f.read()

        # 百度短音频需要 PCM/WAV 格式，MP3 需要先转换
        # 这里假设已经是支持的格式
        body = {
            "format": "mp3",
            "rate": 16000,
            "channel": 1,
            "cuid": "podcast-translator",
            "token": token,
            "dev_pid": 1737,  # 英语
            "speech": base64.b64encode(audio_data).decode(),
            "len": len(audio_data),
        }

        resp = requests.post(self.SHORT_ASR_URL, json=body)
        data = resp.json()

        result = TranscriptResult(language=language)
        if data.get("err_no") == 0:
            text = "".join(data.get("result", []))
            result.full_text = text
            result.segments = [TranscriptSegment(start=0, end=0, text=text)]
            print(f"  ✅ 短音频转写完成")
        else:
            raise RuntimeError(f"百度 STT 失败: {data.get('err_msg')}")

        return result

    def _transcribe_long(self, audio_path: str, language: str) -> TranscriptResult:
        """长音频异步识别"""
        print("     使用长音频异步接口...")
        token = self._get_token()

        # 注意：长音频接口需要音频的公网 URL
        # 实际使用时需要先上传到可访问的地址
        # 这里给出框架
        raise NotImplementedError(
            "百度长音频接口需要公网 URL，请先将音频上传到 OSS，"
            "然后调用 transcribe_with_url 方法。"
        )

    def transcribe_with_url(self, audio_url: str, language: str = "en") -> TranscriptResult:
        """使用公网 URL 进行长音频转写"""
        print(f"  🎤 [百度 STT] 长音频转写: {audio_url[:80]}...")
        token = self._get_token()

        # 创建任务
        body = {
            "speech_url": audio_url,
            "format": "mp3",
            "pid": 1737,  # 英语
            "rate": 16000,
        }
        resp = requests.post(
            f"{self.LONG_ASR_CREATE_URL}?access_token={token}",
            json=body,
        )
        data = resp.json()
        task_id = data.get("task_id")
        print(f"     任务 ID: {task_id}")

        # 轮询结果
        while True:
            resp = requests.post(
                f"{self.LONG_ASR_QUERY_URL}?access_token={token}",
                json={"task_ids": [task_id]},
            )
            qdata = resp.json()
            tasks = qdata.get("tasks_info", [{}])
            status = tasks[0].get("task_status") if tasks else ""

            if status == "Success":
                result_text = tasks[0].get("task_result", {}).get("result", "")
                break
            elif status == "Failed":
                raise RuntimeError(f"百度长音频转写失败: {tasks[0]}")
            else:
                print(f"     状态: {status}，等待...")
                time.sleep(5)

        result = TranscriptResult(language=language)
        # 解析结果 JSON
        try:
            items = json.loads(result_text) if isinstance(result_text, str) else result_text
            if isinstance(items, list):
                for item in items:
                    seg = TranscriptSegment(
                        start=item.get("begin_time", 0) / 1000,
                        end=item.get("end_time", 0) / 1000,
                        text=item.get("result", ""),
                    )
                    result.segments.append(seg)
        except (json.JSONDecodeError, TypeError):
            result.segments = [TranscriptSegment(start=0, end=0, text=str(result_text))]

        result.full_text = " ".join(seg.text for seg in result.segments)
        print(f"  ✅ 转写完成，共 {len(result.segments)} 个片段")
        return result