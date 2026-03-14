"""
providers/oss_storage.py
========================
阿里云 OSS 文件存储（供 TTS 声音克隆使用）
"""

import os
import oss2
from providers.base import StorageProvider


class OSSStorage(StorageProvider):
    """阿里云 OSS"""

    def __init__(self, config: dict):
        super().__init__(config)
        oc = config.get("oss", {})
        self.access_key_id = oc.get("access_key_id", "")
        self.access_key_secret = oc.get("access_key_secret", "")
        self.endpoint = oc.get("endpoint", "")
        self.bucket_name = oc.get("bucket_name", "")
        self.prefix = oc.get("prefix", "podcast-voiceprints/")

        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        self.bucket = oss2.Bucket(auth, self.endpoint, self.bucket_name)

        # 从 endpoint 推断公网域名
        # https://oss-cn-hangzhou.aliyuncs.com -> bucket.oss-cn-hangzhou.aliyuncs.com
        ep = self.endpoint.replace("https://", "").replace("http://", "")
        self.base_url = f"https://{self.bucket_name}.{ep}"

    def upload(self, local_path: str, remote_key: str = None) -> str:
        """
        上传文件到 OSS，返回公网 URL。

        Args:
            local_path: 本地文件路径
            remote_key: OSS 上的路径（默认自动生成）

        Returns:
            公网可访问的 URL
        """
        if remote_key is None:
            # 使用安全的文件名，避免特殊字符和 URL 编码问题
            import re
            import time
            
            filename = os.path.basename(local_path)
            # 清理文件名中的非法字符
            safe_filename = re.sub(r'[^\w\-_.]', '_', filename)
            # 添加时间戳避免重名
            timestamp = int(time.time())
            remote_key = f"{self.prefix}{timestamp}_{safe_filename}"

        print(f"  ☁️  [OSS] 上传：{local_path} -> {remote_key}")

        self.bucket.put_object_from_file(remote_key, local_path)
        url = f"{self.base_url}/{remote_key}"

        print(f"  ✅ 上传完成：{url}")
        return url

    def delete(self, remote_key: str) -> bool:
        """删除 OSS 上的文件"""
        try:
            self.bucket.delete_object(remote_key)
            print(f"  🗑️  [OSS] 已删除: {remote_key}")
            return True
        except Exception as e:
            print(f"  ⚠️  [OSS] 删除失败: {e}")
            return False

    def upload_voiceprint(self, local_path: str, podcast_name: str) -> str:
        """
        上传声纹文件的便捷方法。

        Args:
            local_path: 本地声纹音频路径
            podcast_name: 播客名称（用于生成有意义的文件名）

        Returns:
            公网 URL
        """
        import re
        safe_name = re.sub(r'[^\w\-]', '_', podcast_name)[:50]
        ext = os.path.splitext(local_path)[1] or ".wav"
        remote_key = f"{self.prefix}{safe_name}_voiceprint{ext}"
        return self.upload(local_path, remote_key)

