#!/usr/bin/env python3
"""
八字计算脚本 - 支持命令行调用
"""
import os
import sys
import json
import argparse
from datetime import datetime
from typing import Optional, Dict, Any
import pytz

base = os.path.dirname(__file__)
sys.path.append(base)


from bazi_core import locate_lunar_date


def _load_city_info() -> Dict[str, Dict[str, Any]]:
    base = os.path.dirname(os.path.dirname(__file__))
    p = os.path.join(base, "references", "cities.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


CITY_COORDINATES = _load_city_info()
print(CITY_COORDINATES)

def is_city_supported(city: str) -> bool:
    return city in CITY_COORDINATES


def get_city_info(city: str):
    return CITY_COORDINATES[city]


def compute_bazi_and_dayun(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    gender: str,
    city: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    tz: str = "Asia/Shanghai",
    use_true_solar_time: bool = True,
) -> Dict[str, Any]:
    if city:
        if not is_city_supported(city):
            raise ValueError("城市不支持")
        info = get_city_info(city)
        lat = info["lat"]
        lon = info["lon"]
        tz = info["tz"]
    if lat is None or lon is None:
        raise ValueError("缺少经纬度或城市信息")
    tzobj = pytz.timezone(tz)
    dt_local = tzobj.localize(datetime(year, month, day, hour, minute))
    result = locate_lunar_date(dt_local, lat, lon, tz, use_true_solar_time, gender)
    return result


def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(
        description="八字计算工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python bazi_calculator.py year=1990 month=1 day=1 hour=12 minute=0 gender="男" city="北京"
  python bazi_calculator.py year=1985 month=5 day=15 hour=8 minute=30 gender="女" city="上海"
        """
    )
    
    # 解析 key=value 格式的参数
    for arg in sys.argv[1:]:
        if '=' in arg and not arg.startswith('--'):
            key, value = arg.split('=', 1)
            # 去除引号
            if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            sys.argv[sys.argv.index(arg)] = f'--{key}={value}'
    
    parser.add_argument('--year', type=int, required=True, help='出生年份')
    parser.add_argument('--month', type=int, required=True, help='出生月份 (1-12)')
    parser.add_argument('--day', type=int, required=True, help='出生日期')
    parser.add_argument('--hour', type=int, required=True, help='出生小时 (0-23)')
    parser.add_argument('--minute', type=int, default=0, help='出生分钟 (0-59)')
    parser.add_argument('--gender', type=str, required=True, choices=['男', '女'], help='性别')
    parser.add_argument('--city', type=str, default='北京市', help='城市名称')
    
    args = parser.parse_args()
    
    try:
        result = compute_bazi_and_dayun(
            year=args.year,
            month=args.month,
            day=args.day,
            hour=args.hour,
            minute=args.minute,
            gender=args.gender,
            city=args.city
        )
        
        # 输出JSON格式结果
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
        
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"计算失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

