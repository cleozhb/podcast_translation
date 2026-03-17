"""
providers/cosyvoice_tts.py
==========================
阿里云 CosyVoice 声音克隆 TTS
文档: https://help.aliyun.com/document_detail/2712523.html
"""

import ssl
import os
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

        # 合成质量验证配置
        quality_cfg = cv.get("quality_verify", {})
        self.verify_quality = quality_cfg.get("enabled", False)
        self.similarity_threshold = quality_cfg.get("similarity_threshold", 0.8)
        self.max_verify_retries = quality_cfg.get("max_retries", 3)

        # 音频投票统计：每个特征被投异常票的次数、总投票次数
        self._vote_stats = {
            "total_segments": 0,       # 经过投票的段数
            "feature_abnormal": {},    # {feature_name: abnormal_count}
            "feature_total": {},       # {feature_name: total_count}
            "vote_distribution": {},   # {votes_count: segment_count} e.g. {0: 10, 1: 5, 3: 2}
        }

    def synthesize(self, text: str, output_path: str, voice_url: str = None) -> TTSResult:
        """
        合成语音（带质量验证和自动重试）。

        质量验证失败时不会抛出异常，而是打印醒目警告并返回当前音频，
        避免单段失败导致整个流程中断。

        Args:
            text: 要合成的中文文本
            output_path: 输出音频路径
            voice_url: 声纹参考音频的公网 URL(OSS 地址) 或 音色 ID(voice_id)
        """
        max_attempts = self.max_verify_retries if self.verify_quality else 1
        last_result = None
        last_error = None
        last_detail = ""

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                print(f"     🔄 第 {attempt}/{max_attempts} 次重试合成...")

            try:
                result = self._synthesize_once(text, output_path, voice_url)
                last_result = result
            except Exception as e:
                last_error = e
                continue

            # 质量验证
            if self.verify_quality:
                is_ok, score, detail = self._verify_quality(text, output_path)
                if is_ok:
                    if attempt > 1:
                        print(f"     ✅ 重试成功 (score={score:.2f})")
                    return result
                else:
                    print(f"     ⚠️ 质量不合格 (score={score:.2f}): {detail}")
                    last_detail = detail
                    continue
            else:
                return result

        # 所有重试都失败 —— 降级处理：返回当前音频但标记质量警告
        if last_result:
            last_result.quality_warning = last_detail or str(last_error)
            return last_result

        # 合成本身就失败了（非质量问题），仍需抛出
        if last_error:
            raise last_error
        raise RuntimeError("TTS 合成失败：已达最大重试次数")

    def _synthesize_once(self, text: str, output_path: str, voice_url: str = None) -> TTSResult:
        """
        单次合成语音（不含重试逻辑）。
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
        
    def _verify_quality(self, original_text: str, audio_path: str) -> tuple[bool, float, str]:
        """
        三层质量检测：
          Layer 1: 文本风险评估（零成本）
          Layer 2: 音频多特征投票（零成本）
          Layer 3: STT 反向验证（有成本，仅对可疑段）

        决策矩阵:
          文本低风险 + 音频 ≤1票 → 放行
          文本低风险 + 音频 ≥2票 → STT 验证
          文本中风险 + 音频 ≤1票 → 放行
          文本中风险 + 音频 ≥2票 → STT 验证
          文本高风险              → STT 验证（不看音频）
        """
        # Layer 1: 文本风险
        text_risk, risk_reasons = self._assess_text_risk(original_text)

        if text_risk == "low":
            # Layer 2: 音频特征投票
            try:
                audio_score = self._audio_vote(audio_path)
                votes = audio_score["votes"]
                total = audio_score["total"]

                if votes <= 1:
                    return True, 1.0, f"文本低风险 + 音频正常 ({votes}/{total}票)"
                else:
                    print(f"     🔍 文本低风险但音频可疑 ({votes}/{total}票)，启动 STT 验证...")
            except Exception as e:
                print(f"     ⚠️ 音频检测异常: {e}，放行")
                return True, -1.0, f"音频检测跳过: {e}"

        elif text_risk == "medium":
            # 中风险：也先看音频投票，投票低就放行
            try:
                audio_score = self._audio_vote(audio_path)
                votes = audio_score["votes"]
                total = audio_score["total"]

                if votes <= 1:
                    return True, 0.8, f"文本中风险但音频良好 ({votes}/{total}票)"
                else:
                    print(f"     🔍 文本中风险 ({'; '.join(risk_reasons)}) + 音频 {votes}/{total}票，启动 STT 验证...")
            except Exception as e:
                print(f"     🔍 文本中风险 ({'; '.join(risk_reasons)})，音频检测异常，启动 STT 验证...")

        else:
            # 高风险：直接 STT，不看音频
            print(f"     🔍 文本高风险 ({'; '.join(risk_reasons)})，直接 STT 验证...")

        # Layer 3: STT 反向验证
        try:
            recognized = self._quick_stt(audio_path)
            if not recognized:
                return False, 0.0, "STT 未识别出任何文字"

            score = self._text_similarity(original_text, recognized)
            if score >= self.similarity_threshold:
                return True, score, f"STT 验证通过 ({score:.2f})"
            else:
                recog_short = recognized[:30]
                return False, score, f"STT 相似度低: {score:.2f}, 识别: {recog_short}…"

        except Exception as e:
            if text_risk == "high":
                return False, 0.0, f"高风险段 STT 异常: {e}"
            return True, -1.0, f"STT 跳过: {e}"

    def _audio_vote(self, audio_path: str) -> dict:
        """
        多特征投票检测。
        基于正常/异常音频的统计差异，5 个特征投票。
        """
        import numpy as np
        from pydub import AudioSegment as PydubSeg
        import struct as st

        audio = PydubSeg.from_file(audio_path)
        audio = audio.set_channels(1).set_sample_width(2).set_frame_rate(16000)
        raw = np.array(st.unpack(f"<{len(audio.raw_data)//2}h", audio.raw_data), dtype=np.float32) / 32768.0
        sr = 16000
        duration = len(raw) / sr

        # --- 特征 1: peaks_per_sec ---
        frame_len = int(sr * 0.02)
        hop = frame_len // 2
        energies = [np.sqrt(np.mean(raw[i:i+frame_len]**2)) for i in range(0, len(raw)-frame_len, hop)]
        energies = np.array(energies)
        peaks = []
        if len(energies) > 10:
            sm = np.convolve(energies, np.ones(5)/5, mode='same')
            thresh = np.mean(sm) * 0.5
            for i in range(1, len(sm)-1):
                if sm[i] > sm[i-1] and sm[i] > sm[i+1] and sm[i] > thresh:
                    peaks.append(i)
        peaks_per_sec = len(peaks) / duration if duration > 0 else 0

        # 音节间隔
        interval_mean_ms = np.mean(np.diff(peaks) * 10) if len(peaks) >= 3 else 999

        # --- 特征 2-4: 频谱 ---
        frame_spec = int(sr * 0.03)
        hop_spec = frame_spec // 2
        centroids, fluxes = [], []
        prev = None
        for start in range(0, len(raw)-frame_spec, hop_spec):
            frame = raw[start:start+frame_spec]
            spec = np.abs(np.fft.rfft(frame * np.hanning(len(frame))))
            freqs = np.fft.rfftfreq(len(frame), 1.0/sr)
            if np.sum(spec) > 0:
                centroids.append(np.sum(freqs * spec) / np.sum(spec))
                if prev is not None and len(prev) == len(spec):
                    fluxes.append(np.sqrt(np.mean((spec - prev)**2)))
                prev = spec

        centroid_mean = np.mean(centroids) if centroids else 0
        flux_mean = np.mean(fluxes) if fluxes else 0
        flux_cv = (np.std(fluxes) / np.mean(fluxes)) if fluxes and np.mean(fluxes) > 0 else 0

        # --- 投票 ---
        # 阈值 = 正常均值 + 40% × (异常均值 - 正常均值)
        # 方向: low = 低于阈值为异常, high = 高于阈值为异常
        rules = [
            ("flux_mean",        flux_mean,        "low",  0.49 + 0.4 * (0.31 - 0.49)),      # 0.418
            ("peaks_per_sec",    peaks_per_sec,    "low",  6.62 + 0.4 * (5.15 - 6.62)),      # 6.032
            ("interval_mean_ms", interval_mean_ms, "high", 149.85 + 0.4 * (197.97 - 149.85)),# 169.10
            ("flux_cv",          flux_cv,          "high", 0.89 + 0.4 * (1.04 - 0.89)),      # 0.95
            ("centroid_mean",    centroid_mean,     "high", 1596.66 + 0.4 * (1949.13 - 1596.66)), # 1737.65
        ]

        votes = 0
        details = []
        for name, val, direction, threshold in rules:
            if direction == "high":
                abnormal = val > threshold
            else:
                abnormal = val < threshold
            if abnormal:
                votes += 1
            details.append(f"{'✗' if abnormal else '✓'} {name}={val:.2f} (阈值{threshold:.2f})")

        # 打印每个特征的投票详情
        print(f"     🗳️ 音频投票 ({votes}/{len(rules)}票异常):")
        for d in details:
            print(f"        {d}")

        # 累积统计
        stats = self._vote_stats
        stats["total_segments"] += 1
        stats["vote_distribution"][votes] = stats["vote_distribution"].get(votes, 0) + 1
        for name, val, direction, threshold in rules:
            if direction == "high":
                abnormal = val > threshold
            else:
                abnormal = val < threshold
            stats["feature_total"][name] = stats["feature_total"].get(name, 0) + 1
            if abnormal:
                stats["feature_abnormal"][name] = stats["feature_abnormal"].get(name, 0) + 1

        return {"votes": votes, "total": len(rules), "details": details}

    def print_vote_summary(self):
        """打印音频特征投票统计汇总"""
        stats = self._vote_stats
        total = stats["total_segments"]
        if total == 0:
            return

        print(f"\n{'='*60}")
        print(f"  📊 音频特征投票统计 (共 {total} 段经过投票)")
        print(f"{'='*60}")

        # 投票分布
        print(f"  投票分布:")
        for v in sorted(stats["vote_distribution"].keys()):
            count = stats["vote_distribution"][v]
            pct = count / total * 100
            bar = "█" * int(pct / 2)
            print(f"    {v}票异常: {count:3d}段 ({pct:5.1f}%) {bar}")

        # 各特征异常率
        print(f"  各特征异常率:")
        for name in stats["feature_total"]:
            ft = stats["feature_total"][name]
            fa = stats["feature_abnormal"].get(name, 0)
            pct = fa / ft * 100 if ft > 0 else 0
            print(f"    {name:20s}: {fa:3d}/{ft:3d} ({pct:5.1f}%)")

        print(f"{'='*60}\n")

    @staticmethod
    def _assess_text_risk(text: str) -> tuple[str, list[str]]:
        """
        评估文本送给 TTS 后出乱码的风险。
        纯规则判断，零成本。

        Returns:
            ("low" | "medium" | "high", [原因列表])
        """
        import re
        # 清理 [SPEAKER_xx] 标记，避免误判
        text = re.sub(r'\[SPEAKER_\d+\]\s*', '', text)
        reasons = []
        score = 0

        # 1. 英文字符占比
        total_chars = len(re.sub(r'\s', '', text))
        if total_chars > 0:
            en_chars = len(re.findall(r'[a-zA-Z]', text))
            en_ratio = en_chars / total_chars
            if en_ratio > 0.3:
                score += 3
                reasons.append(f"英文占比高: {en_ratio:.0%}")
            elif en_ratio > 0.15:
                score += 1
                reasons.append(f"英文占比中: {en_ratio:.0%}")

        # 2. 连续英文单词（3个以上连续英文词容易崩）
        long_en = re.findall(r'(?:[a-zA-Z]+[\s\-]){2,}[a-zA-Z]+', text)
        if long_en:
            score += 2
            reasons.append(f"连续英文词组: {len(long_en)}处")

        # 3. 特殊标点密度（引号、破折号、括号嵌套）
        special_puncts = len(re.findall(r'[""''「」『』【】——…]', text))
        if special_puncts > 5:
            score += 2
            reasons.append(f"特殊标点多: {special_puncts}个")
        elif special_puncts > 2:
            score += 1
            reasons.append(f"特殊标点: {special_puncts}个")

        # 4. 单句过长（没有句号的连续文本）
        sentences = re.split(r'[。！？!?]', text)
        max_sent_len = max((len(s.strip()) for s in sentences if s.strip()), default=0)
        if max_sent_len > 100:
            score += 2
            reasons.append(f"超长句: {max_sent_len}字")
        elif max_sent_len > 60:
            score += 1
            reasons.append(f"长句: {max_sent_len}字")

        # 5. 中英文频繁切换（如"用AI来做API的调用"这种）
        switches = len(re.findall(r'[\u4e00-\u9fff][a-zA-Z]|[a-zA-Z][\u4e00-\u9fff]', text))
        if switches > 6:
            score += 2
            reasons.append(f"中英切换频繁: {switches}次")
        elif switches > 3:
            score += 1
            reasons.append(f"中英切换: {switches}次")

        # 6. 数字和特殊格式
        numbers = re.findall(r'\d+(?:\.\d+)?%?', text)
        if len(numbers) > 5:
            score += 1
            reasons.append(f"数字多: {len(numbers)}个")

        # 7. URL 或类 URL 文本
        if re.search(r'https?://|www\.|\.com|\.io', text):
            score += 3
            reasons.append("含 URL")

        # 判定风险等级
        if score >= 5:
            return "high", reasons
        elif score >= 2:
            return "medium", reasons
        else:
            return "low", reasons


    def _check_audio_features(
        self, original_text: str, audio_path: str
    ) -> tuple[bool, float, str]:
        """
        Layer 1: 纯本地音频特征检测，不调用任何 API。

        检测维度:
        1. 音节密度：正常中文约 3-5 音节/秒，对应特定的静音/发声切换频率
        2. 能量稳定性：正常语音能量变化有规律，乱码能量分布异常
        3. 有效语音占比：正常语音中发声段应占合理比例

        所有与语速相关的阈值会自动乘以 self.speech_rate 适配加速/减速场景。

        Returns:
            (is_normal, confidence_score, detail)
        """
        from pydub import AudioSegment, silence
        import struct
        import math
        import re

        audio = AudioSegment.from_file(audio_path)
        duration_sec = len(audio) / 1000

        if duration_sec < 0.5:
            return False, 0.0, "音频过短"

        # 转单声道 16bit 便于分析
        audio = audio.set_channels(1).set_sample_width(2).set_frame_rate(16000)
        samples = struct.unpack(f"<{len(audio.raw_data) // 2}h", audio.raw_data)

        # 预处理原文：去除 [SPEAKER_xx] 标记、标点、空白
        clean_text = re.sub(r'\[SPEAKER_\d+\]', '', original_text)
        clean_text = re.sub(r'\s+', '', clean_text)

        # 语速因子：所有与语速相关的阈值按此倍率缩放
        rate = self.speech_rate

        issues = []
        scores = []
        sub_details = []

        # --- 检测 1: 静音/发声切换频率（音节节奏） ---
        nonsilent = silence.detect_nonsilent(
            audio, min_silence_len=80, silence_thresh=audio.dBFS - 16
        )
        if nonsilent:
            switches_per_sec = len(nonsilent) / duration_sec
            lo = 1.0 * rate
            hi = 12.0 * rate
            if lo <= switches_per_sec <= hi:
                scores.append(1.0)
            else:
                scores.append(0.3)
                issues.append(f"切换频率异常: {switches_per_sec:.1f}/s (期望{lo:.1f}-{hi:.1f})")
            sub_details.append(f"{switches_per_sec:.1f}/s (期望{lo:.1f}-{hi:.1f})")
        else:
            scores.append(0.2)
            issues.append("未检测到语音段")
            sub_details.append("无语音段")

        # --- 检测 2: 能量变化规律性 ---
        frame_size = 800  # 50ms at 16kHz
        frames = [
            samples[i:i + frame_size]
            for i in range(0, len(samples) - frame_size, frame_size)
        ]
        if len(frames) > 4:
            rms_values = []
            for frame in frames:
                rms = math.sqrt(sum(s * s for s in frame) / len(frame)) if frame else 0
                rms_values.append(rms)

            mean_rms = sum(rms_values) / len(rms_values)
            if mean_rms > 0:
                variance = sum((r - mean_rms) ** 2 for r in rms_values) / len(rms_values)
                cv = math.sqrt(variance) / mean_rms

                if 0.3 <= cv <= 2.5:
                    scores.append(1.0)
                else:
                    scores.append(0.3)
                    issues.append(f"能量变异系数异常: {cv:.2f}")
                sub_details.append(f"CV={cv:.2f} (期望0.3-2.5)")
            else:
                scores.append(0.2)
                issues.append("音频能量为零")
                sub_details.append("能量=0")
        else:
            scores.append(0.5)
            sub_details.append("帧数不足")

        # --- 检测 3: 过零率一致性 ---
        if len(frames) > 4:
            zcr_values = []
            for frame in frames:
                crossings = sum(
                    1 for i in range(1, len(frame))
                    if (frame[i] >= 0) != (frame[i - 1] >= 0)
                )
                zcr_values.append(crossings / len(frame) if frame else 0)

            mean_zcr = sum(zcr_values) / len(zcr_values)
            if mean_zcr > 0:
                zcr_var = sum((z - mean_zcr) ** 2 for z in zcr_values) / len(zcr_values)
                zcr_cv = math.sqrt(zcr_var) / mean_zcr

                if 0.2 <= zcr_cv <= 1.8:
                    scores.append(1.0)
                else:
                    scores.append(0.4)
                    issues.append(f"过零率变异异常: {zcr_cv:.2f}")
                sub_details.append(f"CV={zcr_cv:.2f} (期望0.2-1.8)")

        # --- 检测 4: 时长与字数的匹配度 ---
        char_count = len(clean_text)
        if char_count > 0 and duration_sec > 0:
            chars_per_sec = char_count / duration_sec
            lo = 2.0 * rate
            hi = 9.0 * rate
            if lo <= chars_per_sec <= hi:
                scores.append(1.0)
            else:
                scores.append(0.4)
                issues.append(f"语速异常: {chars_per_sec:.1f}字/s (期望{lo:.1f}-{hi:.1f})")
            sub_details.append(f"{chars_per_sec:.1f}字/s (期望{lo:.1f}-{hi:.1f})")

        # 综合评分
        avg_score = sum(scores) / len(scores) if scores else 0
        is_normal = avg_score >= 0.85 and len(issues) == 0

        # 打印每个子维度的分数
        dim_names = ["切换频率", "能量稳定", "过零率", "字速匹配"]
        for i, sd in enumerate(sub_details):
            name = dim_names[i] if i < len(dim_names) else f"维度{i+1}"
            s = scores[i] if i < len(scores) else 0
            mark = "✓" if s >= 0.8 else "✗"
            print(f"       [{name}] {s:.1f}分 {mark}  {sd}")

        detail = "正常" if is_normal else "; ".join(issues)
        return is_normal, avg_score, detail

    def _quick_stt(self, audio_path: str) -> str:
        """
        快速 STT：用 DashScope Recognition 同步识别合成音频。
        只取文本，不需要时间戳。

        Recognition 要求 wav 16kHz 输入，所以先用 pydub 转格式。
        """
        from dashscope.audio.asr import Recognition, RecognitionCallback
        from pydub import AudioSegment
        import tempfile
        import os

        # 转为 wav 16kHz 单声道（Recognition 对 mp3 22050Hz 不稳定）
        audio = AudioSegment.from_file(audio_path)
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        wav_path = tempfile.mktemp(suffix=".wav")
        try:
            audio.export(wav_path, format="wav")

            callback = RecognitionCallback()
            recognition = Recognition(
                model="paraformer-realtime-v2",
                format="wav",
                sample_rate=16000,
                callback=callback,
            )
            result = recognition.call(wav_path)

            if result.status_code == 200:
                sentences = result.get_sentence()
                if sentences:
                    return "".join(s.get("text", "") for s in sentences)
            return ""
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

    @staticmethod
    def _text_similarity(text_a: str, text_b: str) -> float:
        """
        计算两段文本的字符级相似度。
        用编辑距离的归一化版本，不依赖任何外部库。
        """
        # 预处理：去 [SPEAKER_xx] 标记、标点空格，只保留中文和字母数字
        import re
        def clean(t):
            t = re.sub(r'\[SPEAKER_\d+\]', '', t)
            return re.sub(r'[^\u4e00-\u9fff\w]', '', t.lower())

        a = clean(text_a)
        b = clean(text_b)

        if not a or not b:
            return 0.0

        # 用字符级别的 Jaccard 相似度（快速粗略）
        # 对于短文本足够判断是否是乱码
        set_a = set(a)
        set_b = set(b)
        intersection = set_a & set_b
        union = set_a | set_b

        if not union:
            return 0.0

        jaccard = len(intersection) / len(union)

        # 再算一个序列匹配分数（检测顺序是否一致）
        # 用最长公共子序列比率
        lcs_len = CosyVoiceTTS._lcs_length(a[:100], b[:100])  # 限制长度避免慢
        max_len = max(len(a[:100]), len(b[:100]))
        seq_score = lcs_len / max_len if max_len > 0 else 0

        # 综合分数
        return 0.4 * jaccard + 0.6 * seq_score

    @staticmethod
    def _lcs_length(a: str, b: str) -> int:
        """最长公共子序列长度（DP，用于文本相似度计算）"""
        m, n = len(a), len(b)
        if m == 0 or n == 0:
            return 0
        # 空间优化：只用两行
        prev = [0] * (n + 1)
        curr = [0] * (n + 1)
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i - 1] == b[j - 1]:
                    curr[j] = prev[j - 1] + 1
                else:
                    curr[j] = max(prev[j], curr[j - 1])
            prev, curr = curr, [0] * (n + 1)
        return prev[n]