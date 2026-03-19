"""
core/tts_preprocessor.py
========================
TTS 文本预处理：解决 CosyVoice 合成乱码问题。

主要处理：
1. 英文词汇 → 中文读音映射（常见科技词汇表）
2. 英文专有名词 → 保留但加拼读提示
3. 特殊标点清洗
4. 长句自动断句
5. 数字/缩写规范化
"""

import re
from dataclasses import dataclass


# ============================================================
# 英文科技词汇 → 中文读音映射表
# 可根据你翻译的播客领域持续扩充
# ============================================================
ENGLISH_TO_CHINESE_READING = {
    # AI / ML
    "AI": "A I",
    "AGI": "A G I",
    "LLM": "L L M",
    "GPT": "G P T",
    "ML": "M L",
    "NLP": "N L P",
    "API": "A P I",
    "SDK": "S D K",
    "MCP": "M C P",
    "RAG": "R A G",
    "GPU": "G P U",
    "CPU": "C P U",
    "TPU": "T P U",
    "CUDA": "酷达",
    "transformer": "transformer",
    "token": "token",

    # Companies & Products
    "OpenAI": "Open A I",
    "Cursor": "Cursor",
    "Copilot": "Copilot",
    "GitHub": "GitHub",
    "Spotify": "Spotify",
    "Sentry": "Sentry",
    "FanDuel": "FanDuel",
    "Dropbox": "Dropbox",
    "Slack": "Slack",
    "Gmail": "G mail",
    "Google": "谷歌",
    "Apple": "苹果",
    "Meta": "Meta",
    "Microsoft": "微软",
    "Amazon": "亚马逊",
    "Netflix": "奈飞",
    "Tesla": "特斯拉",
    "NVIDIA": "英伟达",

    # People (常见播客主持人/嘉宾)
    "Reid Hoffman": "里德·霍夫曼",
    "Elon Musk": "伊隆·马斯克",
    "Sam Altman": "萨姆·奥特曼",
    "Dario Amodei": "达里奥·阿莫代",
    "Lenny": "莱尼",
    "Boris": "鲍里斯",

    # Tech terms
    "PR": "P R",
    "bug": "bug",
    "app": "app",
    "demo": "demo",
    "CLI": "C L I",
    "Bash": "Bash",
    "Linux": "Linux",
    "iOS": "i O S",
    "Android": "安卓",
    "DevOps": "DevOps",
    "SaaS": "S a a S",
    "B2B": "B to B",
    "CEO": "C E O",
    "CTO": "C T O",
    "VC": "V C",
    "IPO": "I P O",
    "YC": "Y C",

    # Units & common
    "OK": "O K",
    "USB": "U S B",
    "WiFi": "WiFi",
    "URL": "U R L",
    "HTTP": "H T T P",
    "RSS": "R S S",
    "PDF": "P D F",
}


# ============================================================
# 标点清洗规则
# ============================================================

def clean_punctuation(text: str) -> str:
    """
    清洗特殊标点，让 TTS 更容易处理。
    CosyVoice 对中文特殊标点（？！——""等）容易产生乱码，
    需要转为 TTS 友好的等价标点。
    """
    # 破折号 → 逗号（TTS 模型更习惯逗号断句）
    text = re.sub(r'——', '，', text)
    text = re.sub(r'—', '，', text)
    text = re.sub(r'--', '，', text)

    # 中文全角问号/感叹号 → 半角（CosyVoice 对半角更稳定）
    text = text.replace('？', '?')
    text = text.replace('！', '!')

    # 省略号规范化
    text = re.sub(r'…{2,}', '……', text)
    text = re.sub(r'\.{3,}', '……', text)

    # 删除所有引号类标点（引号不影响语音语义，但容易导致 TTS 乱码）
    # 包括：中文单双引号、英文弯引号、直引号、书名号、方括号引用
    text = re.sub(
        '[\u2018\u2019\u201C\u201D\u300C\u300D\u300E\u300F\u3010\u3011\u0027\u0022\u00AB\u00BB]',
        '', text
    )

    # 中文冒号 → 逗号（冒号后的停顿用逗号即可表达）
    text = text.replace('：', '，')

    # 中文分号 → 逗号
    text = text.replace('；', '，')

    # 中文括号 → 逗号（括号结构容易导致 TTS 模型混乱）
    text = text.replace('（', '，')
    text = text.replace('）', '，')
    text = text.replace('(', '，')
    text = text.replace(')', '，')

    # 连续标点去重
    text = re.sub(r'[，,]{2,}', '，', text)
    text = re.sub(r'[。.]{2,}', '。', text)
    text = re.sub(r'[?]{2,}', '?', text)
    text = re.sub(r'[!]{2,}', '!', text)

    # 句首标点清理（删除引号后可能出现句首逗号等）
    text = re.sub(r'(?:^|(?<=\n))[，,]\s*', '', text)

    return text


# ============================================================
# 英文处理
# ============================================================

def process_english_words(text: str, word_map: dict = None) -> str:
    """
    处理文本中的英文单词：
    1. 已知词汇 → 替换为中文读音或标准拼读
    2. 未知的纯大写缩写 → 逐字母拼读（加空格）
    3. 未知的普通英文词 → 保留原样（CosyVoice 对短英文词还算能处理）
    """
    if word_map is None:
        word_map = ENGLISH_TO_CHINESE_READING

    # 先处理多词短语（如 "Claude Code", "Reid Hoffman"）
    # 按长度降序排列，优先匹配长短语
    phrases = sorted(
        [(k, v) for k, v in word_map.items() if ' ' in k],
        key=lambda x: -len(x[0])
    )
    for eng, chn in phrases:
        # 用 word boundary 避免误匹配
        text = re.sub(re.escape(eng), chn, text, flags=re.IGNORECASE)

    # 再处理单词
    def replace_word(match):
        word = match.group(0)
        # 查字典（不区分大小写）
        for eng, chn in word_map.items():
            if ' ' in eng:
                continue  # 短语已处理
            if word.lower() == eng.lower():
                return chn

        # 纯大写缩写（2-5个字母）→ 逐字母拼读
        if re.match(r'^[A-Z]{2,5}$', word):
            return ' '.join(word)

        # 其他英文词保留原样（CosyVoice 对正常英文单词处理较好）
        return word

    # 匹配英文单词（包含字母和常见连字符）
    text = re.sub(r'[A-Za-z][A-Za-z\'-]*[A-Za-z]|[A-Za-z]', replace_word, text)

    return text


# ============================================================
# 数字和特殊格式处理
# ============================================================

def process_numbers(text: str) -> str:
    """将数字转为中文读法，避免 TTS 逐字读数字。"""

    # 百分比
    text = re.sub(r'(\d+(?:\.\d+)?)\s*%', lambda m: f"百分之{m.group(1)}", text)
    text = re.sub(r'百分之百', '百分之百', text)

    # 年份保留（2024年 这种不需要转）

    # 金额
    text = re.sub(r'\$(\d+(?:\.\d+)?)\s*(billion|B)', lambda m: f"{m.group(1)}0亿美元", text, flags=re.IGNORECASE)
    text = re.sub(r'\$(\d+(?:\.\d+)?)\s*(million|M)', lambda m: f"{m.group(1)}00万美元", text, flags=re.IGNORECASE)

    # 常见数字口语化
    text = re.sub(r'(\d+)\s*个', r'\1个', text)  # 保持原样

    return text


# ============================================================
# 合并过短句子
# ============================================================

def merge_short_sentences(text: str, min_chars: int = 10) -> str:
    """
    合并连续短句，避免 TTS 模型因上下文太短而语言跑偏。

    连续的短句（< min_chars 个字）会被用逗号连接成一个较长句子，
    给 TTS 模型更多上下文来判断语言和语调。
    """
    # 按句末标点拆分，保留标点
    parts = re.split(r'([。！？!?])', text)

    # 重组为 (句子, 标点) 对
    sentences = []
    for i in range(0, len(parts), 2):
        sent = parts[i]
        punct = parts[i + 1] if i + 1 < len(parts) else ""
        if sent or punct:
            sentences.append((sent, punct))

    if not sentences:
        return text

    result = []
    buffer = ""  # 累积短句

    for sent, punct in sentences:
        # 计算纯文字长度（去掉空格和标点）
        text_len = len(re.sub(r'[\s，,。！？!?\.\u2026]', '', sent))

        if text_len < min_chars:
            # 短句，累积到 buffer
            if buffer:
                buffer += "，" + sent
            else:
                buffer = sent
        else:
            # 长句，先输出 buffer 中的短句
            if buffer:
                # buffer 里都是短句，和当前长句合并
                result.append(buffer + "，" + sent + punct)
                buffer = ""
            else:
                result.append(sent + punct)

    # 处理剩余的 buffer
    if buffer:
        if result:
            # 把剩余短句附加到最后一个结果
            last = result[-1]
            # 去掉末尾标点，加逗号连接
            last = re.sub(r'[。！？!?]$', '', last)
            result[-1] = last + "，" + buffer + "。"
        else:
            result.append(buffer + "。")

    return ''.join(result)


# ============================================================
# 长句断句
# ============================================================

def split_long_sentences(text: str, max_chars: int = 80) -> str:
    """
    将超长句子在合适的位置断开。
    CosyVoice 对超过 80 个字的单句容易出问题。
    """
    sentences = re.split(r'([。！？!?])', text)
    result = []

    for i in range(0, len(sentences), 2):
        sent = sentences[i]
        # 加回标点
        if i + 1 < len(sentences):
            sent += sentences[i + 1]

        if len(sent) <= max_chars:
            result.append(sent)
            continue

        # 超长句子，在逗号、分号处断开
        parts = re.split(r'([，,；;])', sent)
        current = ""
        for j in range(0, len(parts), 2):
            part = parts[j]
            punct = parts[j + 1] if j + 1 < len(parts) else ""

            if len(current) + len(part) + len(punct) > max_chars and current:
                # 把逗号改成句号，形成自然断句
                if current.endswith('，') or current.endswith(','):
                    current = current[:-1] + '。'
                elif not current.endswith(('。', '！', '？')):
                    current += '。'
                result.append(current)
                current = part + punct
            else:
                current += part + punct

        if current:
            result.append(current)

    return ''.join(result)


# ============================================================
# 主接口
# ============================================================

@dataclass
class PreprocessConfig:
    """预处理配置"""
    # 是否处理英文词汇
    process_english: bool = True
    # 自定义词汇表（追加到默认表）
    custom_word_map: dict = None
    # 是否清洗标点
    clean_punct: bool = True
    # 是否断长句
    split_long: bool = True
    # 长句阈值（字符数）
    max_sentence_chars: int = 80
    # 是否处理数字
    process_nums: bool = True


def preprocess_for_tts(text: str, config: PreprocessConfig = None) -> str:
    """
    TTS 文本预处理主函数。

    Args:
        text: 原始翻译文本
        config: 预处理配置

    Returns:
        清洗后的文本，可直接送给 TTS
    """
    if config is None:
        config = PreprocessConfig()

    original = text

    # Step 1: 数字处理
    if config.process_nums:
        text = process_numbers(text)

    # Step 2: 英文词汇处理
    if config.process_english:
        word_map = dict(ENGLISH_TO_CHINESE_READING)
        if config.custom_word_map:
            word_map.update(config.custom_word_map)
        text = process_english_words(text, word_map)

    # Step 3: 标点清洗
    if config.clean_punct:
        text = clean_punctuation(text)

    # Step 3.5: 合并过短句子（在断长句之前，避免 TTS 因上下文太短跑偏）
    text = merge_short_sentences(text)

    # Step 4: 长句断句
    if config.split_long:
        text = split_long_sentences(text, config.max_sentence_chars)

    # Step 5: 最终清理
    # 去掉多余空格
    text = re.sub(r' {2,}', ' ', text)
    # 去掉行首行尾空格
    # text = '\n'.join(line.strip() for line in text.split('\n'))

    return text


def preprocess_speaker_translations(
    speaker_translations: list[dict],
    config: PreprocessConfig = None,
) -> list[dict]:
    """
    预处理多说话人翻译结果。

    Args:
        speaker_translations: pipeline 中的 speaker_translations 列表
        config: 预处理配置

    Returns:
        处理后的列表（原地修改 translated 字段）
    """
    for item in speaker_translations:
        if item.get("translated"):
            item["translated"] = preprocess_for_tts(item["translated"], config)
    return speaker_translations


# ============================================================
# 调试工具：对比处理前后差异
# ============================================================

def show_diff(original: str, processed: str):
    """打印处理前后的差异，方便调试。"""
    orig_lines = original.strip().split('\n')
    proc_lines = processed.strip().split('\n')

    print("\n  📝 TTS 预处理对比:")
    print("  " + "─" * 60)

    max_lines = max(len(orig_lines), len(proc_lines))
    changes = 0

    for i in range(max_lines):
        o = orig_lines[i] if i < len(orig_lines) else ""
        p = proc_lines[i] if i < len(proc_lines) else ""
        if o != p:
            changes += 1
            # 只显示有变化的行
            if len(o) > 60:
                o = o[:60] + "..."
            if len(p) > 60:
                p = p[:60] + "..."
            print(f"  原文: {o}")
            print(f"  处理: {p}")
            print()

    print(f"  共 {changes} 处变化")
    print("  " + "─" * 60)


if __name__ == "__main__":
    # 测试用例：覆盖各类乱码场景
    test_cases = [
        # 原有测试：破折号、引号、长句
        '我想先抛出一个有点"劲爆"的问题——大概六个月前，不知道大家还记得不记得？你先是离开了Anthropic，加入了Cursor，结果两周后又回了Anthropic。',
        '报告里的原话是："就在我们一眨眼的工夫，AI 已经接管了整个软件开发。"',
        '越来越多经验最丰富、资历最深的工程师——包括你本人——都在公开分享一个事实：自己已经不再写代码了，所有代码都是 AI 生成的。',
        '我们走到这一步，很大程度上，就得益于你当初启动的这个小项目，以及你和团队在过去一年里把它一步步做大的努力。所以我很想听听，你对过去这一年，还有你所做的工作所带来的这些影响，有什么样的思考和感受？',
        # 新增：括号处理
        '太爱了！Gerard K. O\'Neill 的愿景正在变成现实。今年在丰盛峰会（Abundance Summit）现场',
        # 新增：连续短句合并
        '我看见你了。希望。希望。对，就是这个味儿!你这是把希望给商业化了',
        # 新增：破折号+长英文单词
        '好吧，接下来这句话听起来可能有点疯狂——不过没关系',
        '嗯……它之所以叫奇点（singularity），是因为在那个时刻之后，一切都变得不可预测。',
        # 新增：验证 singularity 不被拆成字母
        '这就是所谓的technological singularity，也就是技术奇点。',
    ]

    config = PreprocessConfig()

    print("=" * 65)
    print("  🔧 TTS 预处理测试")
    print("=" * 65)

    for i, text in enumerate(test_cases, 1):
        print(f"\n  [{i}] 原文:")
        print(f"  {text}")
        processed = preprocess_for_tts(text, config)
        print(f"\n  [{i}] 处理后:")
        print(f"  {processed}")
        print(f"\n  {'─' * 60}")