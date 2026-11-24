# bazi-expert 本地运行注意事项（Hermes Desktop / Windows 实测）

## 依赖
- `ephem` 已安装（pip install ephem，Python 3.14 有 wheel）。
- `pytz` 在 Python 3.14 无版本 → scripts/ 下已放 `pytz.py`（zoneinfo shim，模拟 timezone()/localize()/UTC）。**不要删掉它**，否则 import 失败。

## 运行方式
- 从技能根目录运行：`python scripts/bazi_calculator.py year=1971 month=6 day=26 hour=12 minute=0 gender="男" city="无锡"`
- 脚本 stdout 会先打印整个 cities.json（正常现象），JSON 结果在最后。
- 不要用多行 `python -c "..."`（PowerShell 引号/换行转义会报 Invoke-Expression 错）——写个临时 .py 文件再执行。

## 已知坑
- `cities.json` 没有宜兴：用**无锡**近似（经度差 ~0.1°，真太阳时校正差异可忽略）。用户报小城市时先查库，用最近的大城市并告知。
- 用户输入模糊（"午旱"=午时）：按常识解读，午时取 12:00 排盘；信息缺失（如没给出生时间）必须先礼貌询问补全，不得调用排盘脚本（SKILL.md 明确要求）。

## 排盘结果速查（本次会话案例）
- 1971-06-26 午时 男（无锡校正）：四柱 **辛亥 甲午 壬午 丙午**，闰六月初四。
- 壬水日主，午月火旺三午+丙透，**财多身弱**，喜金水（印比），忌火土。
- 起运 6岁7个月，逆排。大运：癸巳→壬辰→辛卯→庚寅→己丑(2018-2028)→戊子(2028-2038)。
- 流年规律：火土旺的月份（5-7月巳午未、10月戌）压力大；金水月（8月申、11-12月亥子）顺。2028 换戊子大运（子水羊刃帮身）是发力期。
