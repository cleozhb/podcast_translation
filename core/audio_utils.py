"""
core/audio_utils.py
===================
音频预处理工具：下载、格式转换、说话人分离、智能声纹提取

说话人分离方案（可插拔）:
  1. pyannote.audio — 本地模型，效果最好，需要 HuggingFace token
  2. DashScope 说话人分离 — 阿里云 API，无需本地 GPU
  3. 简易能量检测 — 不做说话人分离，只做人声/非人声区分（fallback）
"""

import os
import re
import json
import time
import requests
from dataclasses import dataclass, field
from pydub import AudioSegment, silence


# ============================================================
# 数据模型
# ============================================================

@dataclass
class SpeakerSegment:
    """一个说话人的一段连续语音"""
    speaker: str          # 说话人标识，如 "SPEAKER_00"
    start: float          # 开始时间（秒）
    end: float            # 结束时间（秒）

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class DiarizationResult:
    """说话人分离结果"""
    segments: list[SpeakerSegment] = field(default_factory=list)
    num_speakers: int = 0
    # 每个说话人的总时长
    speaker_durations: dict[str, float] = field(default_factory=dict)

    def get_speaker_segments(self, speaker: str) -> list[SpeakerSegment]:
        """获取某个说话人的所有片段"""
        return [s for s in self.segments if s.speaker == speaker]

    def get_longest_continuous(self, speaker: str, min_duration: float = 5.0) -> SpeakerSegment | None:
        """获取某个说话人最长的连续片段"""
        segs = [s for s in self.get_speaker_segments(speaker) if s.duration >= min_duration]
        return max(segs, key=lambda s: s.duration) if segs else None

    def rank_speakers_by_duration(self) -> list[tuple[str, float]]:
        """按总说话时长排序，返回 [(speaker_id, total_seconds), ...]"""
        return sorted(self.speaker_durations.items(), key=lambda x: -x[1])


@dataclass
class VoiceprintInfo:
    """提取的声纹信息"""
    speaker: str            # 说话人标识
    audio_path: str         # 声纹音频文件路径
    duration: float         # 时长（秒）
    source_start: float     # 在原音频中的起始时间
    source_end: float       # 在原音频中的结束时间
    is_host: bool = False   # 是否判定为主持人


# ============================================================
# 下载 & 基础工具
# ============================================================

def download_audio(url: str, output_dir: str, filename: str = None,
                   timeout: int = 120, proxy: str = None) -> str:
    """下载播客音频文件"""
    if not filename:
        name = url.split("/")[-1].split("?")[0]
        if not name or "." not in name:
            name = "episode.mp3"
        filename = name

    filename = re.sub(r'[\\/*?:"<>|]', '_', filename)[:100]
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        print(f"  📁 文件已存在，跳过下载: {filepath}")
        return filepath

    print(f"  📥 下载音频: {url[:80]}...")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0 (PodcastTranslator/1.0)"}

    resp = requests.get(url, stream=True, timeout=timeout,
                        headers=headers, proxies=proxies, allow_redirects=True)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    last_print_time = 0.0
    os.makedirs(output_dir, exist_ok=True)

    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                now = time.monotonic()
                if now - last_print_time >= 0.5:
                    pct = downloaded / total * 100
                    print(f"\r     进度: {pct:.1f}% ({downloaded // (1024*1024)}MB / {total // (1024*1024)}MB)", end="")
                    last_print_time = now

    # 确保下载完成时打印 100%
    if total > 0:
        print(f"\r     进度: 100.0% ({total // (1024*1024)}MB / {total // (1024*1024)}MB)", end="")

    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"\n  ✅ 下载完成: {filepath} ({size_mb:.1f}MB)")
    return filepath


def get_audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）"""
    audio = AudioSegment.from_file(audio_path)
    return len(audio) / 1000


def convert_to_wav(audio_path: str, output_dir: str,
                   sample_rate: int = 16000) -> str:
    """将音频转为单声道 16kHz WAV（说话人分离和 STT 常用格式）"""
    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_channels(1).set_frame_rate(sample_rate)

    basename = os.path.splitext(os.path.basename(audio_path))[0]
    output_path = os.path.join(output_dir, f"{basename}.wav")
    os.makedirs(output_dir, exist_ok=True)

    audio.export(output_path, format="wav")
    return output_path


# ============================================================
# 说话人分离：pyannote.audio（本地，效果最好）
# ============================================================

def diarize_pyannote(audio_path: str, hf_token: str = None,
                     num_speakers: int = None,
                     min_speakers: int = 1,
                     max_speakers: int = 5) -> DiarizationResult:
    """
    使用 pyannote.audio 进行说话人分离。

    需要:
        pip install pyannote.audio torch
        HuggingFace token（模型需要同意使用条款）:
        https://huggingface.co/pyannote/speaker-diarization-3.1

    Args:
        audio_path: 音频文件路径
        hf_token: HuggingFace API token
        num_speakers: 已知的说话人数（None 则自动检测）
        min_speakers: 最少说话人数
        max_speakers: 最多说话人数
    """
    print(f"  🔬 [pyannote] 说话人分离中...")
    print(f"     音频: {audio_path}")

    from pyannote.audio import Pipeline as PyannotePipeline
    import torch

    device = torch.device("mps" if torch.backends.mps.is_available()
                          else "cuda" if torch.cuda.is_available()
                          else "cpu")
    print(f"     设备: {device}")

    pipeline = PyannotePipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )
    pipeline.to(device)

    # 运行分离
    diarization_params = {}
    if num_speakers is not None:
        diarization_params["num_speakers"] = num_speakers
    else:
        diarization_params["min_speakers"] = min_speakers
        diarization_params["max_speakers"] = max_speakers

    diarization = pipeline(audio_path, **diarization_params)

    # 解析结果
    result = DiarizationResult()
    durations = {}

    for turn, _, speaker in diarization.itertracks(yield_label=True):
        seg = SpeakerSegment(
            speaker=speaker,
            start=turn.start,
            end=turn.end,
        )
        result.segments.append(seg)
        durations[speaker] = durations.get(speaker, 0) + seg.duration

    result.num_speakers = len(durations)
    result.speaker_durations = durations

    ranked = result.rank_speakers_by_duration()
    print(f"  ✅ 分离完成: {result.num_speakers} 个说话人")
    for spk, dur in ranked:
        print(f"     {spk}: {dur:.1f}s ({dur / sum(durations.values()) * 100:.0f}%)")

    return result


# ============================================================
# 说话人分离：DashScope（阿里云 API，无需本地 GPU）
# ============================================================

def diarize_dashscope(audio_url: str, api_key: str = None,
                      model: str = "paraformer-v2",
                      language_hints: list[str] = None) -> DiarizationResult:
    """
    使用阿里云 DashScope 说话人分离。

    注意：需要音频的公网 URL（先上传到 OSS）。
    DashScope Paraformer / SenseVoice 支持在转写时同时输出说话人标签。

    Args:
        audio_url: 音频公网 URL
        api_key: DashScope API Key
        model: 转写模型，如 "paraformer-v2" 或 "fun-asr"
        language_hints: 语言提示列表，如 ["en"]、["zh"]
    """
    import dashscope
    from dashscope.audio.asr import Transcription
    import time

    if api_key:
        dashscope.api_key = api_key

    if language_hints is None:
        language_hints = ["en"]

    print(f"  🔬 [DashScope] 说话人分离中...")
    print(f"     模型: {model}")
    print(f"     URL: {audio_url[:80]}...")

    # 使用 Paraformer / SenseVoice 的说话人分离功能
    response = Transcription.async_call(
        model=model,
        file_urls=[audio_url],
        language_hints=language_hints,
        diarization_enabled=True,
    )

    task_id = response.output.get("task_id")
    print(f"     任务 ID: {task_id}")

    # 轮询等待
    while True:
        status = Transcription.fetch(task=task_id)
        task_status = status.output.get("task_status")
        if task_status == "SUCCEEDED":
            break
        elif task_status == "FAILED":
            raise RuntimeError(f"DashScope 分离失败: {status.output}")
        time.sleep(3)
        print("     等待中...")

    # 解析结果
    result = DiarizationResult()
    durations = {}

    results = status.output.get("results", [])
    for r in results:
        url = r.get("transcription_url", "")
        if url:
            resp = requests.get(url)
            data = resp.json()
            for sent in data.get("transcripts", [{}])[0].get("sentences", []):
                spk = sent.get("speaker_id", "SPEAKER_00")
                speaker = f"SPEAKER_{spk:02d}" if isinstance(spk, int) else str(spk)
                seg = SpeakerSegment(
                    speaker=speaker,
                    start=sent.get("begin_time", 0) / 1000,
                    end=sent.get("end_time", 0) / 1000,
                )
                result.segments.append(seg)
                durations[speaker] = durations.get(speaker, 0) + seg.duration

    result.num_speakers = len(durations)
    result.speaker_durations = durations

    print(f"  ✅ 分离完成: {result.num_speakers} 个说话人")
    for spk, dur in result.rank_speakers_by_duration():
        print(f"     {spk}: {dur:.1f}s")

    return result


# ============================================================
# 说话人分离：简易能量检测（Fallback，不区分说话人）
# ============================================================

def diarize_energy(audio_path: str,
                   min_silence_len: int = 700,
                   silence_thresh: int = -40) -> DiarizationResult:
    """
    简易方案：用能量检测找出有人声的片段。
    不区分说话人，全部标记为 SPEAKER_00。
    适合单人播客或只需要提取一个声纹的场景。

    Args:
        audio_path: 音频路径
        min_silence_len: 最短静音长度（毫秒）
        silence_thresh: 静音阈值（dBFS）
    """
    print(f"  🔬 [能量检测] 查找人声片段...")

    audio = AudioSegment.from_file(audio_path)

    # 找出非静音片段
    nonsilent = silence.detect_nonsilent(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
    )

    result = DiarizationResult(num_speakers=1)
    total_dur = 0

    for start_ms, end_ms in nonsilent:
        seg = SpeakerSegment(
            speaker="SPEAKER_00",
            start=start_ms / 1000,
            end=end_ms / 1000,
        )
        result.segments.append(seg)
        total_dur += seg.duration

    result.speaker_durations = {"SPEAKER_00": total_dur}

    print(f"  ✅ 检测到 {len(result.segments)} 个语音片段，总时长 {total_dur:.1f}s")
    return result


# ============================================================
# 智能声纹提取
# ============================================================

def extract_voiceprint(
    audio_path: str,
    output_dir: str,
    diarization: DiarizationResult = None,
    target_speaker: str = None,
    target_duration: float = 20.0,
    min_segment_duration: float = 5.0,
    target_sample_rate: int = 16000,
    skip_initial_seconds: float = 90.0,
) -> list[VoiceprintInfo]:
    """
    智能声纹提取。

    策略：
    1. 如果有说话人分离结果，为每个说话人（或指定说话人）提取声纹
    2. 优先选取最长的连续片段（通常最干净）
    3. 如果单个片段不够长，拼接多个片段至目标时长
    4. 默认为说话最多的人提取（通常是主持人）

    Args:
        audio_path: 源音频路径
        output_dir: 输出目录
        diarization: 说话人分离结果（None 则回退到能量检测）
        target_speaker: 指定说话人 ID（None 则自动选择说话最多的）
        target_duration: 目标声纹时长（秒）
        min_segment_duration: 最短可用片段（秒）
        target_sample_rate: 输出采样率

    Returns:
        VoiceprintInfo 列表（每个说话人一个）
    """
    print(f"  🎵 智能声纹提取")

    os.makedirs(output_dir, exist_ok=True)
    audio = AudioSegment.from_file(audio_path)

    # 如果没有分离结果，用能量检测 fallback
    if diarization is None:
        print("     未提供说话人分离结果，使用能量检测 fallback")
        diarization = diarize_energy(audio_path)

    # 确定要提取声纹的说话人
    if target_speaker:
        speakers_to_extract = [target_speaker]
    else:
        # 按说话时长排序，默认提取所有说话人
        ranked = diarization.rank_speakers_by_duration()
        speakers_to_extract = [spk for spk, _ in ranked]

    basename = os.path.splitext(os.path.basename(audio_path))[0]
    results = []

    for idx, speaker in enumerate(speakers_to_extract):
        segs = diarization.get_speaker_segments(speaker)
        if not segs:
            print(f"     ⚠️ {speaker}: 无片段，跳过")
            continue

        total_dur = diarization.speaker_durations.get(speaker, 0)
        is_host = (idx == 0)  # 说话最多的人判定为主持人

        print(f"     {'🎙️ 主持人' if is_host else '🗣️ 嘉宾  '} {speaker}: "
              f"共 {len(segs)} 个片段，总时长 {total_dur:.1f}s")

        clip = AudioSegment.empty()

        # 策略1: 从中段稳定对谈中选择评分最高的连续窗口
        best_candidate = _select_voiceprint_candidate(
            audio=audio,
            segments=segs,
            target_duration=target_duration,
            min_segment_duration=min_segment_duration,
            skip_initial_seconds=skip_initial_seconds,
        )

        if best_candidate:
            source_start, source_end, score = best_candidate
            clip = audio[int(source_start * 1000): int(source_end * 1000)]
            print(f"     候选窗口评分: {score:.2f} ({source_start:.1f}s-{source_end:.1f}s)")
        else:
            # 策略2: 拼接多个较长片段
            sorted_segs = sorted(
                [s for s in segs if s.end > skip_initial_seconds],
                key=lambda s: -s.duration,
            ) or sorted(segs, key=lambda s: -s.duration)
            clip = AudioSegment.empty()
            source_start = sorted_segs[0].start if sorted_segs else 0
            source_end = source_start

            for seg in sorted_segs:
                if seg.duration < min_segment_duration:
                    continue
                start = max(seg.start + 0.35, skip_initial_seconds)
                end = seg.end - 0.35
                if end <= start:
                    continue
                piece = audio[int(start * 1000): int(end * 1000)]
                clip += piece
                source_end = end
                if len(clip) / 1000 >= target_duration:
                    break

            # 如果还是不够，降低门槛再试
            if len(clip) / 1000 < min_segment_duration:
                for seg in sorted_segs:
                    if seg.duration < 2.0:
                        continue
                    start = max(seg.start + 0.2, skip_initial_seconds)
                    end = seg.end - 0.2
                    if end <= start:
                        continue
                    piece = audio[int(start * 1000): int(end * 1000)]
                    clip += piece
                    source_end = end
                    if len(clip) / 1000 >= target_duration:
                        break

        if len(clip) < 2000:  # 不到 2 秒
            print(f"     ⚠️ {speaker}: 可用语音太短，跳过")
            continue

        # 截取到目标时长
        if len(clip) / 1000 > target_duration:
            clip = clip[:int(target_duration * 1000)]

        # 处理：首尾静音裁剪 + 轻微归一化 + fade，最后转单声道/采样率
        clip = _prepare_voiceprint_clip(clip)
        clip = clip.set_channels(1).set_frame_rate(target_sample_rate).set_sample_width(2)

        # 输出
        output_path = os.path.join(output_dir, f"{basename}_{speaker}_voiceprint.wav")
        clip.export(output_path, format="wav")

        info = VoiceprintInfo(
            speaker=speaker,
            audio_path=output_path,
            duration=len(clip) / 1000,
            source_start=source_start,
            source_end=source_end,
            is_host=is_host,
        )
        results.append(info)
        print(f"     ✅ {output_path} ({info.duration:.1f}s)"
              f" {'[主持人]' if is_host else '[嘉宾]'}")

    return results


def _select_voiceprint_candidate(
    audio: AudioSegment,
    segments: list[SpeakerSegment],
    target_duration: float,
    min_segment_duration: float,
    skip_initial_seconds: float,
) -> tuple[float, float, float] | None:
    """Choose a stable middle-conversation window for voice cloning."""
    candidates: list[tuple[float, float, float]] = []
    boundary_pad = 0.4
    window = target_duration

    for seg in segments:
        start = max(seg.start + boundary_pad, skip_initial_seconds)
        end = seg.end - boundary_pad
        usable = end - start
        if usable < min_segment_duration:
            continue
        if usable >= window:
            # Test a few windows rather than blindly taking exact middle.
            starts = {start, start + (usable - window) / 2, end - window}
            for cand_start in sorted(starts):
                cand_end = cand_start + window
                clip = audio[int(cand_start * 1000): int(cand_end * 1000)]
                score = _score_voiceprint_window(clip, cand_start, skip_initial_seconds)
                candidates.append((score, cand_start, cand_end))
        else:
            clip = audio[int(start * 1000): int(end * 1000)]
            score = _score_voiceprint_window(clip, start, skip_initial_seconds)
            candidates.append((score - 0.15, start, end))

    if not candidates:
        return None
    score, start, end = max(candidates, key=lambda x: x[0])
    return start, end, score


def _score_voiceprint_window(clip: AudioSegment, start_sec: float, skip_initial_seconds: float) -> float:
    """Heuristic score for clean voiceprint windows."""
    duration_sec = len(clip) / 1000.0
    if duration_sec <= 0:
        return -999.0

    nonsilent = silence.detect_nonsilent(
        clip,
        min_silence_len=350,
        silence_thresh=clip.dBFS - 18 if clip.dBFS != float("-inf") else -45,
    )
    voiced_ms = sum(end - start for start, end in nonsilent)
    voiced_ratio = voiced_ms / max(1, len(clip))
    silence_penalty = max(0.0, 0.85 - voiced_ratio)

    loudness = clip.dBFS if clip.dBFS != float("-inf") else -60.0
    loudness_score = 1.0 - min(1.0, abs(loudness + 20.0) / 25.0)
    clipping_penalty = 0.4 if clip.max_dBFS > -0.2 else 0.0
    early_penalty = 0.2 if start_sec <= skip_initial_seconds + 10 else 0.0

    return (
        0.45 * voiced_ratio
        + 0.35 * loudness_score
        + 0.20 * min(1.0, duration_sec / 20.0)
        - silence_penalty
        - clipping_penalty
        - early_penalty
    )


def _prepare_voiceprint_clip(clip: AudioSegment) -> AudioSegment:
    """Prepare a conservative voiceprint clip for enrollment."""
    if len(clip) <= 0:
        return clip

    nonsilent = silence.detect_nonsilent(
        clip,
        min_silence_len=250,
        silence_thresh=clip.dBFS - 18 if clip.dBFS != float("-inf") else -45,
    )
    if nonsilent:
        start_ms = max(0, nonsilent[0][0] - 80)
        end_ms = min(len(clip), nonsilent[-1][1] + 80)
        clip = clip[start_ms:end_ms]

    if clip.dBFS != float("-inf"):
        target_dbfs = -20.0
        gain = max(-6.0, min(6.0, target_dbfs - clip.dBFS))
        clip = clip.apply_gain(gain)

    fade_ms = min(50, max(10, len(clip) // 20))
    return clip.fade_in(fade_ms).fade_out(fade_ms)


# ============================================================
# 高级接口：一键完成说话人分离 + 声纹提取
# ============================================================

def extract_voiceprints_auto(
    audio_path: str,
    output_dir: str,
    method: str = "energy",
    target_duration: float = 20.0,
    min_segment_duration: float = 5.0,
    target_sample_rate: int = 16000,
    skip_initial_seconds: float = 90.0,
    # pyannote 参数
    hf_token: str = None,
    num_speakers: int = None,
    # dashscope 参数
    audio_url: str = None,
    dashscope_api_key: str = None,
    dashscope_model: str = "paraformer-v2",
    language_hints: list[str] = None,
) -> tuple[list[VoiceprintInfo], DiarizationResult]:
    """
    一键接口：自动完成说话人分离 + 声纹提取。

    Returns:
        (voiceprints, diarization_result) 元组：声纹列表 + 完整说话人时间线

    Args:
        audio_path: 本地音频路径
        output_dir: 输出目录
        method: 分离方法 "pyannote" | "dashscope" | "energy"
        target_duration: 每个声纹目标时长
        min_segment_duration: 最短候选片段时长
        target_sample_rate: 输出声纹采样率
        skip_initial_seconds: 默认跳过开头不稳定区域
        hf_token: pyannote 所需的 HuggingFace token
        num_speakers: 已知说话人数（None 自动检测）
        audio_url: dashscope 所需的公网 URL
        dashscope_api_key: DashScope API Key
        dashscope_model: dashscope 转写模型，如 "paraformer-v2" 或 "fun-asr"
        language_hints: 语言提示列表，如 ["en"]、["zh"]

    Returns:
        每个说话人的 VoiceprintInfo 列表
    """
    print(f"\n  🎯 自动声纹提取 (方法: {method})")

    # 先转为 WAV（部分方法需要）
    wav_path = audio_path
    if not audio_path.endswith(".wav"):
        wav_dir = os.path.join(output_dir, "_temp")
        wav_path = convert_to_wav(audio_path, wav_dir)
        print(f"     已转换为 WAV: {wav_path}")

    # Step 1: 说话人分离
    if method == "pyannote":
        if not hf_token:
            raise ValueError("pyannote 方法需要 hf_token 参数（HuggingFace token）")
        diarization = diarize_pyannote(
            wav_path, hf_token=hf_token, num_speakers=num_speakers
        )
    elif method == "dashscope":
        if not audio_url:
            raise ValueError("dashscope 方法需要 audio_url 参数（公网 URL）")
        diarization = diarize_dashscope(
            audio_url, api_key=dashscope_api_key,
            model=dashscope_model, language_hints=language_hints,
        )
    elif method == "energy":
        diarization = diarize_energy(wav_path)
    else:
        raise ValueError(f"未知分离方法: {method}，可选: pyannote / dashscope / energy")

    # Step 2: 提取声纹
    voiceprints = extract_voiceprint(
        audio_path=audio_path,
        output_dir=output_dir,
        diarization=diarization,
        target_duration=target_duration,
        min_segment_duration=min_segment_duration,
        target_sample_rate=target_sample_rate,
        skip_initial_seconds=skip_initial_seconds,
    )

    # 总结
    print(f"\n  📊 声纹提取总结:")
    print(f"     说话人数: {diarization.num_speakers}")
    for vp in voiceprints:
        role = "主持人" if vp.is_host else "嘉宾"
        print(f"     {vp.speaker} [{role}]: {vp.duration:.1f}s -> {vp.audio_path}")

    return voiceprints, diarization
