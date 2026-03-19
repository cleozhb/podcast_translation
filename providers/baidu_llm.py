# -*- coding: utf-8 -*-
"""
providers/baidu_llm.py
======================
百度千帆 文心一言 翻译
文档: https://cloud.baidu.com/doc/WENXINWORKSHOP/index.html
"""

import requests
from providers.base import LLMProvider, TranslationResult


class BaiduLLM(LLMProvider):
    """百度千帆 文心一言"""

    BASE_URL = "https://qianfan.baidubce.com/v2/chat/completions"

    def __init__(self, config: dict):
        super().__init__(config)
        bc = config.get("baidu", {})
        self.api_key = bc.get("api_key", "")
        self.model = bc.get("llm_model", "deepseek-v3.2")

    def translate(self, text: str, system_prompt: str = "") -> TranslationResult:
        print(f"  🌐 [百度 LLM] 翻译中... (模型: {self.model})")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": text})

        body = {
            "model": self.model,
            "messages": messages,
        }

        resp = requests.post(self.BASE_URL, headers=headers, json=body)
        data = resp.json()

        if "choices" in data and len(data["choices"]) > 0:
            translated = data["choices"][0]["message"]["content"]
            print(f"  ✅ 翻译完成，译文 {len(translated)} 字")
            return TranslationResult(
                source_text=text,
                translated_text=translated,
            )
        else:
            raise RuntimeError(f"百度 LLM 失败: {data.get('error_msg', data)}")


def main():
    import yaml
    import os

    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # 创建 BaiduLLM 实例
    llm = BaiduLLM(config)
    text = '''这些数字简直太疯狂了，对吧？全球所有代码提交里有整整百分之四都来自我们——这可远远超出了我最初的想象。而且正如你刚才说的，这还只是个起点。另外要注意，这些数据只统计了公开代码仓库里的代码提交。如果我们把私有代码仓库也算上，实际比例还会高得多。  
对我来说，最震撼的其实倒不是当前这个数字本身，而是我们的增长速度——你看 Quad Code 在任何维度上的增长曲线，都是在持续加速的。它不只是在上升，而是在越来越快地上升。  
我刚开始做 Quad Code 的时候，它本来就是个小小的技术实验，一个随手做的小 hack。当时我们在 Anthropic 就有个大致方向：我们想推出一款面向编程场景的产品。而 Anthropic 很长时间以来，都在以一种特定方式构建大模型——这种思路跟我们对“如何安全地构建通用人工智能 AGI”的理解是一致的：先让模型在编程能力上做到顶尖，再让它精通各种工具的使用，最后再让它真正学会操作整台电脑。大致就是这样一个发展路径，而且我们为此已经投入研究很多年了。  
再看我最初组建的那个团队，名字叫 Anthropic Labs 团队。实际上，迈克·克里格和本·曼最近又重启了这个团队，开启了第二轮攻坚。这个团队做了不少很酷的东西：比如 Quad Code、比如 MCP、比如桌面端应用。你可以明显看出这条演进脉络：从写代码，到用工具，再到操作整台电脑。  
这对 Anthropic 来说之所以特别重要，核心还是安全性。归根结底，还是回到这一点——AI 越来越强大，能力也越来越强。过去这一年里发生的一个关键变化是：至少对工程师来说，AI 已经不只是帮你写写代码、聊聊天那么简单了，它真的开始调用工具、真正地在现实世界中采取行动了。  
而随着 Cowork 的推出，我们正开始看到这种转变向非技术用户蔓延。对很多习惯使用对话式 AI 的人来说，这可能是他们第一次真正用上一个能“主动做事”的 AI——它能帮你收发 Gmail 邮件，能接入你的 Slack，还能替你完成一大堆日常任务，而且干得相当不错。而且它的表现，只会越来越好。  
所以长期以来，在 Anthropic 内部一直有种感觉：我们很想做出点什么'''

    # 测试翻译
    result = llm.translate(text,"总结这段话")
    print('原文:', result.source_text)
    print('译文:', result.translated_text)


if __name__ == "__main__":
    main()