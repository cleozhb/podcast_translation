"""
core/shownote_generator.py
==========================
从 RSS 提取原始 Shownote + 基于转写/翻译自动生成中文 Shownote。

生成内容：
  1. 节目总结（1-2 段）
  2. 嘉宾介绍
  3. 时间线（带时间戳的章节标题）
  4. 关键要点（bullet points）
  5. 原始链接和引用
"""

import re
import html
from dataclasses import dataclass, field
from providers.base import LLMProvider, TranscriptResult


@dataclass
class ShownoteResult:
    """生成的 Shownote"""
    title_zh: str = ""                 # 中文标题
    summary: str = ""                  # 总结
    guest_intro: str = ""              # 嘉宾介绍
    timeline: list[dict] = field(default_factory=list)  # [{time, title, summary}]
    key_points: list[str] = field(default_factory=list)  # 关键要点
    original_shownote: str = ""        # 原始英文 shownote
    links: list[dict] = field(default_factory=list)  # [{text, url}]
    full_text: str = ""                # 完整 shownote 文本

    def to_markdown(self) -> str:
        """输出 Markdown 格式"""
        lines = []

        if self.title_zh:
            lines.append(f"# {self.title_zh}\n")

        if self.summary:
            lines.append(f"{self.summary}\n")

        if self.guest_intro:
            lines.append(f"## 嘉宾介绍\n\n{self.guest_intro}\n")

        if self.timeline:
            lines.append("## 时间线\n")
            for t in self.timeline:
                ts = t.get("time", "")
                title = t.get("title", "")
                summary = t.get("summary", "")
                if summary:
                    lines.append(f"- **{ts}** {title} — {summary}")
                else:
                    lines.append(f"- **{ts}** {title}")
            lines.append("")

        if self.key_points:
            lines.append("## 关键要点\n")
            for p in self.key_points:
                lines.append(f"- {p}")
            lines.append("")

        if self.links:
            lines.append("## 相关链接\n")
            for l in self.links:
                lines.append(f"- [{l['text']}]({l['url']})")
            lines.append("")

        if self.original_shownote:
            lines.append("---\n")
            lines.append("## 原始 Shownote\n")
            lines.append(self.original_shownote)

        return "\n".join(lines)

    def to_plain_text(self) -> str:
        """输出适合小宇宙等平台的纯文本格式（不含 Markdown 语法）"""
        lines = []

        if self.title_zh:
            lines.append(f"【{self.title_zh}】\n")

        if self.summary:
            lines.append(f"{self.summary}\n")

        if self.guest_intro:
            lines.append(f"〖嘉宾介绍〗\n{self.guest_intro}\n")

        if self.timeline:
            lines.append("〖时间线〗")
            for t in self.timeline:
                ts = t.get("time", "")
                title = t.get("title", "")
                summary = t.get("summary", "")
                if summary:
                    lines.append(f"  {ts}  {title} — {summary}")
                else:
                    lines.append(f"  {ts}  {title}")
            lines.append("")

        if self.key_points:
            lines.append("〖关键要点〗")
            for p in self.key_points:
                lines.append(f"  · {p}")
            lines.append("")

        if self.links:
            lines.append("〖相关链接〗")
            for l in self.links:
                lines.append(f"  · {l['text']}: {l['url']}")
            lines.append("")

        lines.append("---")
        lines.append("本节目由 AI 翻译自英文原版播客，仅供学习交流。")

        return "\n".join(lines)


# ============================================================
# 从 RSS 条目提取原始 Shownote
# ============================================================

def extract_shownote_from_entry(entry: dict) -> dict:
    """
    从 feedparser 的 entry 中提取 shownote 相关信息。

    Returns:
        {
            "title": str,
            "description": str,       # 纯文本描述
            "description_html": str,   # HTML 描述（如果有）
            "links": [{text, url}],    # 提取的链接
            "published": str,
            "duration": str,
        }
    """
    result = {
        "title": entry.get("title", ""),
        "published": entry.get("published", ""),
        "duration": entry.get("itunes_duration", ""),
    }

    # 描述：优先取 content:encoded，其次 description，再次 summary
    desc_html = ""
    if "content" in entry and entry["content"]:
        desc_html = entry["content"][0].get("value", "")
    elif "description" in entry:
        desc_html = entry["description"]
    elif "summary" in entry:
        desc_html = entry["summary"]

    result["description_html"] = desc_html
    result["description"] = _html_to_text(desc_html)

    # 提取链接
    result["links"] = _extract_links(desc_html)

    return result


def _html_to_text(html_str: str) -> str:
    """简单的 HTML → 纯文本转换"""
    if not html_str:
        return ""
    # 去 HTML 标签
    text = re.sub(r'<br\s*/?>|</p>|</li>|</div>', '\n', html_str, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    # 解码 HTML 实体
    text = html.unescape(text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_links(html_str: str) -> list[dict]:
    """从 HTML 中提取链接"""
    if not html_str:
        return []
    links = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_str, re.IGNORECASE):
        url = match.group(1)
        text = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        if url and text and not url.startswith('#') and not url.startswith('mailto:'):
            links.append({"text": text, "url": url})
    return links


# ============================================================
# 用 LLM 生成中文 Shownote
# ============================================================

SHOWNOTE_PROMPT = """你是一位专业的播客编辑，擅长为中文听众撰写播客节目介绍。
请根据以下信息，生成一份结构化的中文 Shownote。

要求：
1. 用中文写作，风格简洁有力，适合播客平台（如小宇宙）展示
2. 严格按照指定的 JSON 格式输出，不要输出任何其他内容
3. 时间线处理原则：
   - 如果输入中提供了"原始章节时间线"，**必须**原样沿用其时间戳（英文原版时间），仅翻译标题并补一句话摘要，不要改动、重排或编造时间戳
   - 如果没有提供原始章节时间线，才从带时间戳的转写内容中归纳 5-8 个章节，时间戳必须使用"英文原版时间"（即转写中给出的时间），后续程序会自动映射到中文音频时间
4. 总结控制在 100-200 字，不要流水账，要抓住核心价值
5. 关键要点提炼 3-5 条最有价值的观点或信息

输出格式（严格 JSON）：
{
  "title_zh": "中文标题",
  "summary": "节目总结，100-200字",
  "guest_intro": "嘉宾介绍，如果有嘉宾的话",
  "timeline": [
    {"time": "00:00", "title": "章节标题", "summary": "一句话概括"},
    {"time": "05:30", "title": "章节标题", "summary": "一句话概括"}
  ],
  "key_points": [
    "要点一",
    "要点二",
    "要点三"
  ]
}
"""


def parse_original_timeline(desc: str) -> list[dict]:
    """从原始英文 shownote 描述中解析章节时间线。

    匹配常见格式：
      "0:00 - Intro"
      "4:37 Manifolds"
      "1:10:29 - Juan's background"

    Returns: [{"time": "MM:SS"或"H:MM:SS", "time_sec": float, "title": str}, ...]
    """
    if not desc:
        return []

    entries = []
    # 每行单独解析；时间戳必须在行首附近
    pattern = re.compile(
        r'^\s*(?:[-*•]\s*)?'
        r'((?:\d{1,2}:)?\d{1,2}:\d{2})'
        r'\s*[-–—:]?\s*'
        r'(.+?)\s*$'
    )
    for line in desc.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        ts = m.group(1)
        title = m.group(2).strip()
        if not title or len(title) > 120:
            continue
        sec = _parse_timestamp(ts)
        entries.append({"time": ts, "time_sec": sec, "title": title})

    # 合理性检查：至少 3 条 且 时间递增
    if len(entries) < 3:
        return []
    for i in range(1, len(entries)):
        if entries[i]["time_sec"] < entries[i - 1]["time_sec"]:
            return []
    return entries


def generate_shownote(
    llm: LLMProvider,
    transcript: TranscriptResult,
    translation_text: str,
    original_shownote: dict = None,
    podcast_name: str = "",
    episode_title: str = "",
) -> ShownoteResult:
    """
    用 LLM 生成中文 Shownote。

    Args:
        llm: LLM provider
        transcript: 带时间戳的转写结果
        translation_text: 中文翻译文本
        original_shownote: 从 RSS 提取的原始 shownote
        podcast_name: 播客名称
        episode_title: 节目标题
    """
    print(f"  📝 生成 Shownote...")

    # 构造输入
    input_parts = []
    input_parts.append(f"播客名称: {podcast_name}")
    input_parts.append(f"原标题: {episode_title}")

    parsed_timeline = []
    if original_shownote:
        desc = original_shownote.get("description", "")
        if desc:
            parsed_timeline = parse_original_timeline(desc)
            # 截取原始描述的前 1500 字，避免超长
            input_parts.append(f"\n原始英文 Shownote:\n{desc[:1500]}")

    if parsed_timeline:
        input_parts.append("\n原始章节时间线（权威，请原样沿用时间戳，仅翻译标题）:")
        for t in parsed_timeline:
            input_parts.append(f"  {t['time']} {t['title']}")

    # 提供带时间戳的转写文本摘要（取关键时间点）
    if transcript and transcript.segments:
        input_parts.append("\n带时间戳的内容概要:")
        # 每隔一段取样，给 LLM 感知时间线
        total_segs = len(transcript.segments)
        sample_indices = range(0, total_segs, max(1, total_segs // 20))
        for i in sample_indices:
            seg = transcript.segments[i]
            ts = _format_timestamp(seg.start)
            speaker = f"[{seg.speaker}] " if seg.speaker else ""
            text_preview = seg.text[:100]
            input_parts.append(f"  {ts} {speaker}{text_preview}")

    # 提供翻译文本（截取前 3000 字）
    if translation_text:
        input_parts.append(f"\n中文翻译文本（节选）:\n{translation_text[:3000]}")

    user_input = "\n".join(input_parts)

    # 调用 LLM
    result = llm.translate(user_input, SHOWNOTE_PROMPT)
    response_text = result.translated_text

    # 解析 JSON
    shownote = _parse_shownote_response(response_text)

    # 补充原始信息
    if original_shownote:
        shownote.original_shownote = original_shownote.get("description", "")
        shownote.links = original_shownote.get("links", [])

    shownote.full_text = shownote.to_plain_text()

    print(f"  ✅ Shownote 生成完成")
    print(f"     标题: {shownote.title_zh}")
    print(f"     时间线: {len(shownote.timeline)} 个章节")
    print(f"     要点: {len(shownote.key_points)} 条")

    return shownote


def _parse_shownote_response(text: str) -> ShownoteResult:
    """解析 LLM 返回的 JSON"""
    import json

    # 提取 JSON（可能被包在 ```json 里）
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    result = ShownoteResult()

    try:
        data = json.loads(text)
        result.title_zh = data.get("title_zh", "")
        result.summary = data.get("summary", "")
        result.guest_intro = data.get("guest_intro", "")
        result.key_points = data.get("key_points", [])

        for t in data.get("timeline", []):
            result.timeline.append({
                "time": t.get("time", ""),
                "title": t.get("title", ""),
                "summary": t.get("summary", ""),
            })

    except json.JSONDecodeError as e:
        print(f"  ⚠️ Shownote JSON 解析失败: {e}")
        # 回退：直接用原文
        result.summary = text[:500]

    return result


def _format_timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ============================================================
# 时间线时间戳校正（对齐到中文音频）
# ============================================================

def _parse_timestamp(ts: str) -> float:
    """解析 "MM:SS" 或 "H:MM:SS" 为秒数"""
    parts = ts.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return 0.0


def _map_timestamp(original_sec: float, segment_map: list[dict]) -> float:
    """基于段落映射表，将原始时间戳线性插值到中文音频时间戳。

    segment_map: [{original_start, original_end, chinese_start, chinese_end}, ...]
    """
    if not segment_map:
        return original_sec

    # 查找包含此时间戳的段落
    for seg in segment_map:
        if seg["original_start"] <= original_sec <= seg["original_end"]:
            orig_range = seg["original_end"] - seg["original_start"]
            if orig_range <= 0:
                return seg["chinese_start"]
            ratio = (original_sec - seg["original_start"]) / orig_range
            return seg["chinese_start"] + ratio * (seg["chinese_end"] - seg["chinese_start"])

    # 未命中：找最近的段落
    closest = min(segment_map, key=lambda s: abs(s["original_start"] - original_sec))
    # 用比例外推
    if closest["original_end"] > closest["original_start"]:
        offset = original_sec - closest["original_start"]
        scale = (closest["chinese_end"] - closest["chinese_start"]) / (closest["original_end"] - closest["original_start"])
        return closest["chinese_start"] + offset * scale
    return closest["chinese_start"]


def adjust_timeline(shownote: ShownoteResult, segment_map: list[dict]) -> None:
    """将 shownote 时间线的时间戳映射到中文音频时间（原地修改）。"""
    if not segment_map or not shownote.timeline:
        return
    for entry in shownote.timeline:
        original_sec = _parse_timestamp(entry.get("time", ""))
        chinese_sec = _map_timestamp(original_sec, segment_map)
        entry["time"] = _format_timestamp(chinese_sec)