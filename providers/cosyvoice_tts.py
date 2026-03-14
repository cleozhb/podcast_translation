"""
providers/cosyvoice_tts.py
==========================
阿里云 CosyVoice 声音克隆 TTS
文档: https://help.aliyun.com/document_detail/2712523.html
"""

import ssl
import os
import time
import certifi

# ============================================================
# 修复 macOS Python 3.12 SSL 证书问题
# DashScope TTS 内部使用 websocket-client 库建立 WSS 连接，
# 需要同时 patch websocket 库的 SSL 上下文
# ============================================================
_CA_BUNDLE = certifi.where()
os.environ["SSL_CERT_FILE"] = _CA_BUNDLE
os.environ["WEBSOCKET_CLIENT_CA_BUNDLE"] = _CA_BUNDLE
os.environ["REQUESTS_CA_BUNDLE"] = _CA_BUNDLE

# Patch websocket-client 的默认 SSL 上下文
try:
    import websocket._http as _ws_http
    
    # 方法 1: 直接设置全局 SSL 上下文
    import websocket
    _orig_create = websocket.WebSocket.__init__

    def _patched_init(self, *args, **kwargs):
        if "sslopt" not in kwargs:
            kwargs["sslopt"] = {}
        if "ca_certs" not in kwargs["sslopt"]:
            kwargs["sslopt"]["ca_certs"] = _CA_BUNDLE
        if "cert_reqs" not in kwargs["sslopt"]:
            kwargs["sslopt"]["cert_reqs"] = ssl.CERT_REQUIRED
        _orig_create(self, *args, **kwargs)

    websocket.WebSocket.__init__ = _patched_init
    print("  🔧 已 patch websocket SSL 证书配置")
except Exception as e:
    print(f"  ⚠️ websocket SSL patch 失败 (可忽略): {e}")


import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, VoiceEnrollmentService
from providers.base import TTSProvider, TTSResult


class CosyVoiceTTS(TTSProvider):
    """阿里云 CosyVoice 声音克隆"""

    def __init__(self, config: dict):
        super().__init__(config)
        cv = config.get("cosyvoice", {})
        dashscope.api_key = cv.get("api_key", "")
        self.model = cv.get("model", "cosyvoice-v1")
        
        # 音色缓存：{voiceprint_url: voice_id}
        # 为每个不同的声纹 URL 缓存对应的音色 ID
        self._voice_cache = {}
        
        # 语音合成参数配置
        # 语速：0.5-2.0，默认 1.0（正常语速）
        self.speech_rate = cv.get("speech_rate", 1.0)
        # 音调：0.5-2.0，默认 1.0（原调）
        self.pitch_rate = cv.get("pitch_rate", 1.0)
        # 音量：0-100，默认 50
        self.volume = cv.get("volume", 50)
        # 声音克隆相关配置 - 移除了持久化的 voice_id 和 voiceprint_url
        # 每次都为不同的声纹创建新音色（多说话人场景）

    def synthesize(self, text: str, output_path: str, voice_url: str = None) -> TTSResult:
        """
        合成语音。

        Args:
            text: 要合成的中文文本
            output_path: 输出音频路径
            voice_url: 声纹参考音频的公网 URL(OSS 地址) 或 音色 ID(voice_id)
        """
        print(f"  🔊 [CosyVoice TTS] 合成中...")
        print(f"     模型：{self.model}")
        
        # CosyVoice 参数 - 只设置必要的参数
        params = {
            "model": self.model,
        }

        # 判断 voice_url 是 voice_id 还是 OSS URL
        # voice_id 通常是以 "cosyvoice-" 或包含下划线的字符串
        if voice_url and voice_url.strip():
            if voice_url.startswith("http://") or voice_url.startswith("https://"):
                # 这是 OSS URL，尝试自动创建音色
                print(f"     检测到 OSS URL，尝试自动创建音色...")
                
                # 检查缓存中是否已有该声纹的音色
                if voice_url in self._voice_cache:
                    voice_id = self._voice_cache[voice_url]
                    print(f"     使用缓存音色ID: {voice_id}")
                else:
                    # 为该声纹创建新音色
                    voice_id = self._create_voice_from_url(voice_url)
                    if voice_id:
                        self._voice_cache[voice_url] = voice_id
                
                if voice_id:
                    print(f"     使用音色ID: {voice_id}")
                    params["voice"] = voice_id
                else:
                    # 创建失败，降级到默认音色
                    print(f"  ⚠️ 音色创建失败，降级到默认音色 'longxiaochun'")
                    params["voice"] = "longxiaochun"
            elif voice_url.startswith("cosyvoice-") or "_" in voice_url:
                # 这可能是 voice_id
                print(f"     使用音色ID: {voice_url[:60]}...")
                params["voice"] = voice_url
            else:
                # 其他情况，使用默认音色
                print(f"     使用默认音色：longxiaochun")
                params["voice"] = "longxiaochun"
        else:
            # 使用预置音色
            params["voice"] = "longxiaochun"  # 默认音色
            print(f"     使用默认音色：longxiaochun")

        try:
            # 添加语音控制参数到已有的 params 中
            # 语速：0.5-2.0，默认 1.0
            if self.speech_rate != 1.0:
                params["speech_rate"] = self.speech_rate
            # 音调：0.5-2.0，默认 1.0
            if self.pitch_rate != 1.0:
                params["pitch_rate"] = self.pitch_rate
            # 音量：0-100，默认 50
            if self.volume != 50:
                params["volume"] = self.volume
            
            print(f"     语速：{self.speech_rate}x, 音调：{self.pitch_rate}x, 音量：{self.volume}")
            
            synthesizer = SpeechSynthesizer(**params)
            
            # 非流式调用：call() 方法直接返回完整的二进制音频数据 (bytes)
            audio_data = synthesizer.call(text)
            
            # 检查是否返回了有效的音频数据
            if audio_data is None:
                raise RuntimeError(
                    "CosyVoice API 调用返回 None,请检查:\n"
                    f"  1. API Key 是否正确\n"
                    f"  2. 模型 '{self.model}' 是否与音色版本匹配\n"
                    f"  3. 音色ID 是否有效 (如果使用声音克隆)\n"
                    f"\n💡 重要提示:"
                    f"\n   - cosyvoice-v1 不支持声音克隆功能"
                    f"\n   - 如需使用声音克隆，请升级到 cosyvoice-v2 或 cosyvoice-v3-plus"
                    f"\n   - 系统会自动从 OSS URL 创建音色，无需手动运行工具"
                )
            
            if not isinstance(audio_data, bytes):
                raise RuntimeError(f"CosyVoice 返回了意外数据类型：{type(audio_data)}")
            
            if len(audio_data) == 0:
                raise RuntimeError("CosyVoice 未返回音频数据")

            # 写入文件
            with open(output_path, "wb") as f:
                f.write(audio_data)

            print(f"  ✅ TTS 完成：{output_path}")
            print(f"     音频大小：{len(audio_data)} bytes")

            return TTSResult(
                audio_path=output_path,
                sample_rate=22050,
            )
            
        except Exception as e:
            print(f"  ❌ TTS 合成失败：{e}")
            print(f"     错误类型：{type(e).__name__}")
            # 重新抛出异常以便上层处理
            raise
            
    def _create_voice_from_url(self, audio_url: str, prefix: str = None) -> str:
        """
        从音频 URL 创建音色
        
        Args:
            audio_url: 音频的公网 URL
            prefix: 音色前缀 (可选，默认自动生成)
            
        Returns:
            voice_id: 创建成功的音色ID，失败返回 None
        """
        import time
        from dashscope.audio.tts_v2 import VoiceEnrollmentService
        
        # 检查模型是否支持声音克隆
        if self.model not in ["cosyvoice-v2", "cosyvoice-v3-plus", "cosyvoice-v3-flash", "cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash"]:
            print(f"  ⚠️  {self.model} 不支持声音克隆，需升级到 cosyvoice-v2 或更高版本")
            return None
        
        try:
            print(f"  🎨 正在创建音色...")
            print(f"     音频 URL: {audio_url[:80]}...")
            print(f"     目标模型：{self.model}")
            
            service = VoiceEnrollmentService()
            
            # 如果没有指定前缀，使用时间戳
            if not prefix:
                import random
                prefix = f"voice{random.randint(1000, 9999)}"
            
            # Step 1: 创建音色
            voice_id = service.create_voice(
                target_model=self.model,
                prefix=prefix,
                url=audio_url
            )
            
            print(f"  ✅ 音色创建成功!")
            print(f"     Voice ID: {voice_id}")
            
            # Step 2: 等待音色就绪
            print(f"  ⏳ 等待音色就绪...", end="", flush=True)
            max_attempts = 30
            
            for attempt in range(1, max_attempts + 1):
                try:
                    voice_info = service.query_voice(voice_id=voice_id)
                    status = voice_info.get("status")
                    
                    if status == "OK":
                        print(f"✅ (耗时 {attempt * 10}s)")
                        return voice_id
                    elif status == "UNDEPLOYED":
                        print(f"\n  ❌ 音色创建失败，请检查音频质量")
                        return None
                    elif status == "FAILED":
                        print(f"\n  ❌ 音色创建失败：{voice_info}")
                        return None
                    else:
                        print(f".", end="", flush=True)
                        time.sleep(10)
                        
                except Exception as e:
                    print(f"查询出错：{e}")
                    time.sleep(5)
            
            print(f"\n  ⚠️ 等待超时，音色可能仍在处理中")
            return None
            
        except Exception as e:
            print(f"  ❌ 创建音色失败：{e}")
            return None

    def preload_voices(self, voiceprint_urls: list[str]) -> dict[str, str]:
        """
        预创建多个声纹的音色
        
        Args:
            voiceprint_urls: 声纹 URL 列表
            
        Returns:
            {voiceprint_url: voice_id} 映射字典
        """
        if not voiceprint_urls:
            return {}
        
        print(f"\n  🎨 预创建 {len(voiceprint_urls)} 个音色...")
        result = {}
        
        for i, url in enumerate(voiceprint_urls, 1):
            print(f"\n  [{i}/{len(voiceprint_urls)}] ", end="")
            voice_id = self._create_voice_from_url(url)
            if voice_id:
                result[url] = voice_id
                self._voice_cache[url] = voice_id
        
        print(f"\n  ✅ 预创建完成，成功 {len(result)}/{len(voiceprint_urls)} 个音色")
        return result

    def synthesize_long(
        self,
        text: str,
        output_path: str,
        voice_url: str = None,
        max_chars: int = 500,
    ) -> TTSResult:
        """
        长文本合成：自动分段合成后拼接。
        CosyVoice 单次合成有字数限制，长文本需要分段。

        Args:
            text: 完整中文文本
            output_path: 最终输出路径
            voice_url: 声纹 URL
            max_chars: 每段最大字数
        """
        from pydub import AudioSegment
        import tempfile
        import os

        # 按句子分段，每段不超过 max_chars
        chunks = self._split_text(text, max_chars)
        print(f"  🔊 [CosyVoice TTS] 长文本合成，共 {len(chunks)} 段")

        combined = AudioSegment.empty()
        temp_files = []

        try:
            for i, chunk in enumerate(chunks, 1):
                print(f"     合成第 {i}/{len(chunks)} 段 ({len(chunk)} 字)...")
                temp_path = tempfile.mktemp(suffix=".mp3")
                temp_files.append(temp_path)

                try:
                    self.synthesize(chunk, temp_path, voice_url)
                    segment = AudioSegment.from_file(temp_path)
                    combined += segment
                except Exception as e:
                    print(f"  ❌ 第 {i} 段合成失败：{e}")
                    print(f"     本段文本：{chunk[:100]}...")
                    raise

            # 导出拼接后的完整音频
            combined.export(output_path, format="mp3")
            duration = len(combined) / 1000

            print(f"  ✅ 长文本 TTS 完成：{output_path} ({duration:.1f}s)")
            return TTSResult(audio_path=output_path, duration=duration)

        finally:
            for f in temp_files:
                if os.path.exists(f):
                    os.remove(f)

    @staticmethod
    def _split_text(text: str, max_chars: int) -> list[str]:
        """按句号、叹号、问号分段，每段不超过 max_chars"""
        import re
        sentences = re.split(r'([。！？!?])', text)

        chunks = []
        current = ""
        for i in range(0, len(sentences), 2):
            sent = sentences[i]
            # 加上标点
            if i + 1 < len(sentences):
                sent += sentences[i + 1]

            if len(current) + len(sent) > max_chars and current:
                chunks.append(current.strip())
                current = sent
            else:
                current += sent

        if current.strip():
            chunks.append(current.strip())

        return chunks if chunks else [text]