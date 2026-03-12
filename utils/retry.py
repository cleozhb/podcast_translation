"""指数退避重试装饰器。"""

import functools
import time
from loguru import logger


def retry(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0):
    """指数退避重试装饰器，适用于 API 调用等可能临时失败的操作。"""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"{func.__name__} 第 {attempt + 1} 次失败: {e}，{delay:.1f}s 后重试"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} 重试 {max_retries} 次后仍然失败: {e}"
                        )
            raise last_exception

        return wrapper

    return decorator
