"""pytz 兼容 shim：Python 3.14 无 pytz 版本可用，用标准库 zoneinfo 模拟其接口。

用法：放在 scripts/ 目录（sys.path 已包含该目录），脚本 `import pytz` 时自动加载本模块。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")


class _LocalizedZone(ZoneInfo):
    """在 zoneinfo 基础上补上 pytz 的 localize() 方法。"""

    def localize(self, dt: datetime) -> datetime:
        """给 naive datetime 附上本时区；已是 aware 则原样返回。"""
        if dt.tzinfo is not None:
            return dt
        return dt.replace(tzinfo=self)


def timezone(name: str) -> _LocalizedZone:
    if name in ("UTC", "Etc/UTC"):
        return UTC
    return _LocalizedZone(name)
