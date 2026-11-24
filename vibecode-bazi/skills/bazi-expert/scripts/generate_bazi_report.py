#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八字报告生成器 - 优化版
支持五行颜色映射和灵活的喜忌神显示
支持命令行调用：python generate_bazi_report.py input.json [output.html]
"""

import datetime

def get_beijing_now():
    """获取当前北京时间"""
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=8)

import os
import sys
import json
import argparse

# 天干地支五行映射
ELEMENT_MAP = {
    # 天干
    "甲": "wood", "乙": "wood",
    "丙": "fire", "丁": "fire",
    "戊": "earth", "己": "earth",
    "庚": "metal", "辛": "metal",
    "壬": "water", "癸": "water",
    # 地支
    "子": "water", "丑": "earth",
    "寅": "wood", "卯": "wood",
    "辰": "earth", "巳": "fire",
    "午": "fire", "未": "earth",
    "申": "metal", "酉": "metal",
    "戌": "earth", "亥": "water"
}

# 五行对应的能量条颜色
ELEMENT_COLORS = {
    "wood": "#3e753b",   # 竹青
    "fire": "#d64036",   # 丹红
    "earth": "#8c6450",  # 赭石
    "metal": "#b08d28",  # 铜金
    "water": "#3d6e85"   # 鸦青
}

def get_element(char):
    """获取天干或地支的五行属性"""
    return ELEMENT_MAP.get(char, "earth")

def get_energy_bar_color(day_gan):
    """根据日主天干获取能量条颜色"""
    element = get_element(day_gan)
    return ELEMENT_COLORS.get(element, "#8c6450")

def format_favorable_gods(gods_list):
    """格式化喜用神标签"""
    if not gods_list:
        return '<span class="element-tag is-good">均衡为宜</span>'
    
    html = ""
    for god in gods_list:
        html += f'<span class="element-tag is-good">{god}</span>'
    return html

def format_unfavorable_section(gods_list):
    """格式化忌神部分，如果没有忌神则不显示"""
    if not gods_list:
        return ""
    
    html = '<div style="margin-bottom: 15px;">'
    html += '<strong style="color: var(--fire);">⚠️ 您的忌神（需防）：</strong><br>'
    html += '<div style="margin-top: 8px;">'
    for god in gods_list:
        html += f'<span class="element-tag is-bad">{god}</span>'
    html += '</div></div>'
    return html

def normalize_gender(gender_input):
    """
    规范化性别输入，统一转换为"男"或"女"
    
    支持的输入格式：
    - 女性：女、女命、女生、女性、姑娘、女士、美女、女人
    - 男性：男、男命、男生、男性、先生、男士、帅哥、男人
    - 其他：默认返回"男"
    
    参数:
    gender_input: 用户输入的性别（字符串）
    
    返回:
    "男" 或 "女"
    """
    if not gender_input:
        return "男"
    
    gender_str = str(gender_input).strip()
    
    # 女性关键词
    female_keywords = ["女", "女命", "女生", "女性", "姑娘", "女士", "美女", "女人", "女士", "小姐", "妹子"]
    
    # 男性关键词
    male_keywords = ["男", "男命", "男生", "男性", "先生", "男士", "帅哥", "男人", "兄弟", "哥们"]
    
    # 判断是否为女性
    for keyword in female_keywords:
        if keyword in gender_str:
            return "女"
    
    # 判断是否为男性
    for keyword in male_keywords:
        if keyword in gender_str:
            return "男"
    
    # 默认返回男
    return "男"

def generate_report(bazi_info, template_path=None):
    """
    生成八字报告HTML
    
    参数:
    bazi_info: 字典，包含以下信息
        - user_name: 用户姓名
        - gender: 性别
        - bazi: 八字四柱 {"year_gan": "癸", "year_zhi": "卯", ...}
        - strength_level: 旺衰级别
        - score_percent: 能量条百分比
        - month_support: 得令分析
        - root_support: 得地分析
        - help_support: 得势分析
        - favorable_gods: 喜用神列表
        - unfavorable_gods: 忌神列表
        - method_name: 取用方法
        - reasoning: 取用思路
        - lucky_colors: 幸运颜色
        - lucky_directions: 有利方位
        - suitable_industries: 适合行业
        - specific_advice: 特别建议
        - current_dayun: 当前大运字典
            - name: 大运名称（如"壬子"）
            - age_range: 年龄范围（如"28-37"）
            - gan_analysis: 天干分析
            - zhi_analysis: 地支分析
            - overall: 整体评价
            - features: 大运特点
            - strategy: 策略建议
        - year_2026: 2026年运势字典
        - year_2027: 2027年运势字典
        - year_2028: 2028年运势字典
    template_path: 模板文件路径，默认使用 assets/report_template.html
    """
    
    # 确定模板路径
    if template_path is None:
        # 获取脚本所在目录的上级目录（Skill根目录）
        script_dir = os.path.dirname(os.path.abspath(__file__))
        skill_root = os.path.dirname(script_dir)
        template_path = os.path.join(skill_root, "assets", "report_template.html")
    
    # 读取模板
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    
    # 基本信息
    data = {
        "USER_NAME": bazi_info.get("user_name", "匿名用户"),
        "GENDER": normalize_gender(bazi_info.get("gender", "男")),
        "REPORT_DATE": datetime.get_beijing_now().strftime("%Y年%m月%d日"),
    }
    
    # 八字四柱及五行颜色
    bazi = bazi_info.get("bazi", {})
    data.update({
        "YEAR_GAN": bazi.get("year_gan", ""),
        "YEAR_ZHI": bazi.get("year_zhi", ""),
        "MONTH_GAN": bazi.get("month_gan", ""),
        "MONTH_ZHI": bazi.get("month_zhi", ""),
        "DAY_GAN": bazi.get("day_gan", ""),
        "DAY_ZHI": bazi.get("day_zhi", ""),
        "HOUR_GAN": bazi.get("hour_gan", ""),
        "HOUR_ZHI": bazi.get("hour_zhi", ""),
        
        # 五行颜色
        "YEAR_GAN_ELEMENT": get_element(bazi.get("year_gan", "")),
        "YEAR_ZHI_ELEMENT": get_element(bazi.get("year_zhi", "")),
        "MONTH_GAN_ELEMENT": get_element(bazi.get("month_gan", "")),
        "MONTH_ZHI_ELEMENT": get_element(bazi.get("month_zhi", "")),
        "DAY_GAN_ELEMENT": get_element(bazi.get("day_gan", "")),
        "DAY_ZHI_ELEMENT": get_element(bazi.get("day_zhi", "")),
        "HOUR_GAN_ELEMENT": get_element(bazi.get("hour_gan", "")),
        "HOUR_ZHI_ELEMENT": get_element(bazi.get("hour_zhi", "")),
    })
    
    # 强弱定性
    day_element_map = {
        "甲": "甲木（阳木）", "乙": "乙木（阴木）",
        "丙": "丙火（阳火）", "丁": "丁火（阴火）",
        "戊": "戊土（阳土）", "己": "己土（阴土）",
        "庚": "庚金（阳金）", "辛": "辛金（阴金）",
        "壬": "壬水（阳水）", "癸": "癸水（阴水）",
    }
    
    data.update({
        "DAY_ELEMENT": day_element_map.get(bazi.get("day_gan", ""), ""),
        "STRENGTH_LEVEL": bazi_info.get("strength_level", "中和"),
        "SCORE_PERCENT": str(bazi_info.get("score_percent", 50)),
        "ENERGY_BAR_COLOR": get_energy_bar_color(bazi.get("day_gan", "")),
        "MONTH_SUPPORT": bazi_info.get("month_support", ""),
        "ROOT_SUPPORT": bazi_info.get("root_support", ""),
        "HELP_SUPPORT": bazi_info.get("help_support", ""),
    })
    
    # 喜用神（灵活处理）
    favorable_gods = bazi_info.get("favorable_gods", [])
    unfavorable_gods = bazi_info.get("unfavorable_gods", [])
    
    data.update({
        "FAVORABLE_GODS": format_favorable_gods(favorable_gods),
        "UNFAVORABLE_SECTION": format_unfavorable_section(unfavorable_gods),
        "METHOD_NAME": bazi_info.get("method_name", ""),
        "REASONING_SUMMARY": bazi_info.get("reasoning", ""),
    })
    
    # 生活指南
    data.update({
        "LUCKY_COLORS": bazi_info.get("lucky_colors", ""),
        "LUCKY_DIRECTIONS": bazi_info.get("lucky_directions", ""),
        "SUITABLE_INDUSTRIES": bazi_info.get("suitable_industries", ""),
        "SPECIFIC_ADVICE": bazi_info.get("specific_advice", ""),
    })
    
    # 当前大运分析
    dayun_info = bazi_info.get("current_dayun", {})
    data.update({
        "DAYUN_NAME": dayun_info.get("name", ""),
        "DAYUN_AGE_RANGE": dayun_info.get("age_range", ""),
        "DAYUN_GAN_ANALYSIS": dayun_info.get("gan_analysis", ""),
        "DAYUN_ZHI_ANALYSIS": dayun_info.get("zhi_analysis", ""),
        "DAYUN_OVERALL": dayun_info.get("overall", ""),
        "DAYUN_FEATURES": dayun_info.get("features", ""),
        "DAYUN_STRATEGY": dayun_info.get("strategy", ""),
    })
    
    # 未来三年流年运势
    for year in [2026, 2027, 2028]:
        year_data = bazi_info.get(f"year_{year}", {})
        prefix = f"YEAR_{year}_"
        data.update({
            f"{prefix}CLASS": year_data.get("class", "normal"),
            f"{prefix}STARS": year_data.get("stars", "★★★☆☆"),
            f"{prefix}SUMMARY": year_data.get("summary", ""),
            f"{prefix}CAREER": year_data.get("career", ""),
            f"{prefix}WEALTH": year_data.get("wealth", ""),
            f"{prefix}LOVE": year_data.get("love", ""),
            f"{prefix}HEALTH": year_data.get("health", ""),
            f"{prefix}TIP": year_data.get("tip", ""),
        })
    
    # 替换占位符
    for key, value in data.items():
        template = template.replace(f'{{{key}}}', str(value))
    
    return template


def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(
        description="八字HTML报告生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从项目根目录调用
  python scripts/generate_bazi_report.py ./user-data/bazi_info.json ./user-data/bazi_report.html

  # 从 scripts 目录调用
  python generate_bazi_report.py ../user-data/bazi_info.json ../user-data/bazi_report.html
        """
    )
    
    parser.add_argument('input_file', type=str, help='输入的bazi_info JSON文件路径')
    parser.add_argument('output_file', type=str, help='输出的HTML文件路径')
    
    args = parser.parse_args()
    
    try:
        # 读取输入JSON文件
        if not os.path.exists(args.input_file):
            print(f"错误: 输入文件不存在: {args.input_file}", file=sys.stderr)
            return 1
        
        with open(args.input_file, 'r', encoding='utf-8') as f:
            bazi_info = json.load(f)
        
        # 生成报告
        html = generate_report(bazi_info)
        
        # 写入HTML文件
        output_path = args.output_file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        
        print(f"报告已生成: {output_path}")
        return 0
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"报告生成失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    main()
