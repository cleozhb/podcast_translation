#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
声音克隆工具 - 创建和使用自定义音色
=====================================
使用方法:
1. 准备声纹音频文件 (10-30 秒，清晰朗读，无背景音乐)
2. 上传到 OSS 获取公网 URL
3. 运行此脚本创建音色
4. 将返回的 voice_id 配置到 config.yaml

注意：
- cosyvoice-v1 不支持声音克隆，需要升级到 cosyvoice-v2 或 cosyvoice-v3-plus
- 每个阿里云账号最多可创建 1000 个音色
"""

import os
import sys
import time
import yaml


def create_voice_clone(audio_url: str, prefix: str = "myvoice", target_model: str = "cosyvoice-v2"):
    """
    创建声音克隆音色
    
    Args:
        audio_url: 声纹音频的公网 URL
        prefix: 音色前缀 (仅小写字母和数字，<10 字符)
        target_model: 目标模型 (推荐 cosyvoice-v2 或 cosyvoice-v3-plus)
    
    Returns:
        voice_id: 音色ID
    """
    import dashscope
    from dashscope.audio.tts_v2 import VoiceEnrollmentService
    
    print("=" * 60)
    print("🎨 创建声音克隆音色")
    print("=" * 60)
    print(f"音频 URL: {audio_url}")
    print(f"音色前缀：{prefix}")
    print(f"目标模型：{target_model}")
    print()
    
    try:
        service = VoiceEnrollmentService()
        
        # Step 1: 创建音色
        print("Step 1: 提交音色创建请求...")
        voice_id = service.create_voice(
            target_model=target_model,
            prefix=prefix,
            url=audio_url
        )
        print(f"✅ 音色创建成功!")
        print(f"   Voice ID: {voice_id}")
        print(f"   Request ID: {service.get_last_request_id()}")
        print()
        
        # Step 2: 等待音色就绪
        print("Step 2: 等待音色就绪...")
        max_attempts = 30
        for attempt in range(1, max_attempts + 1):
            try:
                voice_info = service.query_voice(voice_id=voice_id)
                status = voice_info.get("status")
                
                print(f"   [{attempt}/{max_attempts}] 状态：{status}")
                
                if status == "OK":
                    print(f"\n✅ 音色已就绪！")
                    break
                elif status == "UNDEPLOYED":
                    print(f"\n❌ 音色创建失败，请检查音频质量")
                    return None
                elif status == "FAILED":
                    print(f"\n❌ 音色创建失败：{voice_info}")
                    return None
                
                time.sleep(10)  # 每 10 秒查询一次
                
            except Exception as e:
                print(f"   查询失败：{e}")
                time.sleep(5)
        else:
            print(f"\n⚠️ 等待超时，音色可能仍在处理中")
            print(f"   请稍后手动查询：voice_id = {voice_id}")
        
        return voice_id
        
    except Exception as e:
        print(f"❌ 创建失败：{e}")
        import traceback
        traceback.print_exc()
        return None


def test_voice_synthesis(voice_id: str, text: str = "你好，这是测试语音。", model: str = "cosyvoice-v2"):
    """
    测试音色合成
    
    Args:
        voice_id: 音色ID
        text: 测试文本
        model: 模型版本
    """
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer
    
    print("\n" + "=" * 60)
    print("🔊 测试音色合成")
    print("=" * 60)
    print(f"Voice ID: {voice_id}")
    print(f"模型：{model}")
    print(f"文本：{text}")
    print()
    
    try:
        synthesizer = SpeechSynthesizer(model=model, voice=voice_id)
        audio_data = synthesizer.call(text)
        
        if audio_data and len(audio_data) > 0:
            output_path = f"test_voice_{voice_id}.mp3"
            with open(output_path, "wb") as f:
                f.write(audio_data)
            
            print(f"✅ 合成成功!")
            print(f"   输出文件：{output_path}")
            print(f"   音频大小：{len(audio_data)} bytes")
            return True
        else:
            print(f"❌ 未返回音频数据")
            return False
            
    except Exception as e:
        print(f"❌ 合成失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    # 加载配置
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        print(f"❌ 配置文件不存在：{config_path}")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    cosyvoice_config = config.get("cosyvoice", {})
    oss_config = config.get("oss", {})
    
    api_key = cosyvoice_config.get("api_key", "")
    current_model = cosyvoice_config.get("model", "cosyvoice-v1")
    
    print("\n" + "=" * 60)
    print("🎙️ CosyVoice 声音克隆工具")
    print("=" * 60)
    print(f"当前模型：{current_model}")
    print()
    
    # 检查模型版本
    if current_model == "cosyvoice-v1":
        print("⚠️  警告：cosyvoice-v1 不支持声音克隆功能!")
        print("   建议升级到 cosyvoice-v2 或 cosyvoice-v3-plus")
        print()
        choice = input("是否继续？(y/N): ").strip().lower()
        if choice != 'y':
            print("已取消操作")
            sys.exit(0)
        
        # 提示用户升级模型
        print("\n请选择要使用的模型:")
        print("1. cosyvoice-v2 (推荐，兼容性好)")
        print("2. cosyvoice-v3-plus (最佳音质，需申请)")
        print("3. 继续使用 cosyvoice-v1 (仅使用默认音色)")
        
        model_choice = input("\n选择 (1/2/3): ").strip()
        if model_choice == "1":
            target_model = "cosyvoice-v2"
        elif model_choice == "2":
            target_model = "cosyvoice-v3-plus"
            print("\n⚠️  注意：cosyvoice-v3-plus 需要先申请额度")
            print("   请访问阿里云百炼控制台申请")
        else:
            print("\n使用默认音色模式，不进行声音克隆")
            sys.exit(0)
    else:
        target_model = current_model
    
    # 设置 API Key
    dashscope.api_key = api_key
    
    # 获取声纹音频 URL
    print("\n请提供声纹音频的公网 URL:")
    print("   要求：10-30 秒，清晰朗读，无背景音乐")
    print("   格式：WAV/MP3/M4A, ≤10MB, ≥16kHz")
    print()
    
    # 如果配置了 OSS，可以尝试上传
    if oss_config.get("access_key_id"):
        print("检测到 OSS 配置，是否上传本地音频文件？")
        use_oss = input("是否使用 OSS 上传？(y/N): ").strip().lower()
        
        if use_oss == 'y':
            audio_file = input("请输入本地音频文件路径：").strip()
            if os.path.exists(audio_file):
                # 上传到 OSS
                try:
                    import oss2
                    auth = oss2.Auth(
                        oss_config["access_key_id"],
                        oss_config["access_key_secret"]
                    )
                    bucket = oss2.Bucket(
                        auth,
                        oss_config["endpoint"],
                        oss_config["bucket_name"]
                    )
                    
                    remote_key = f"{oss_config['prefix']}{os.path.basename(audio_file)}"
                    print(f"正在上传：{audio_file} -> {remote_key}")
                    bucket.put_object_from_file(remote_key, audio_file)
                    
                    # 构建公网 URL
                    ep = oss_config["endpoint"].replace("https://", "").replace("http://", "")
                    audio_url = f"https://{oss_config['bucket_name']}.{ep}/{remote_key}"
                    print(f"✅ 上传成功!")
                    print(f"   公网 URL: {audio_url}")
                    
                except Exception as e:
                    print(f"❌ 上传失败：{e}")
                    audio_url = input("请手动输入音频 URL: ").strip()
            else:
                print(f"❌ 文件不存在：{audio_file}")
                audio_url = input("请手动输入音频 URL: ").strip()
        else:
            audio_url = input("请输入音频 URL: ").strip()
    else:
        audio_url = input("请输入音频 URL: ").strip()
    
    if not audio_url:
        print("❌ 音频 URL 不能为空")
        sys.exit(1)
    
    # 创建音色
    prefix = input("\n请输入音色前缀 (默认 myvoice): ").strip() or "myvoice"
    
    voice_id = create_voice_clone(audio_url, prefix, target_model)
    
    if voice_id:
        print("\n" + "=" * 60)
        print("✅ 声音克隆创建完成!")
        print("=" * 60)
        print(f"Voice ID: {voice_id}")
        print()
        print("下一步操作:")
        print(f"1. 更新 config.yaml 中的 model 为 '{target_model}'")
        print(f"2. 在代码中使用 voice='{voice_id}' 参数")
        print()
        
        # 询问是否测试
        test_choice = input("是否立即测试音色合成？(y/N): ").strip().lower()
        if test_choice == 'y':
            test_text = input("请输入测试文本 (默认：你好，这是测试语音。): ").strip() or "你好，这是测试语音。"
            test_voice_synthesis(voice_id, test_text, target_model)
        
        print("\n💡 使用提示:")
        print(f"   在代码中：SpeechSynthesizer(model='{target_model}', voice='{voice_id}')")
        print(f"   或在 config.yaml 中保存 voice_id 配置")


if __name__ == "__main__":
    main()
