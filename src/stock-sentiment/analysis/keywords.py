# -*- coding: utf-8 -*-
"""关键词提取：jieba 分词 + 财经停用词过滤 + 自定义财经词典"""
import os
import re
from collections import Counter

import jieba
import jieba.posseg  # noqa: F401  注册 posseg 模块

DICT_PATH = os.path.join(os.path.dirname(__file__), "finance_dict.txt")

# 财经自定义词典（jieba 用户词典，确保这些词不被切碎）
FINANCE_WORDS = [
    "贵州茅台 100 n", "东方财富 100 n", "比亚迪 100 n", "宁德时代 100 n",
    "中芯国际 100 n", "隆基绿能 100 n", "中国平安 100 n", "招商银行 100 n",
    "五粮液 100 n", "恒瑞医药 100 n", "兆易创新 100 n", "利欧股份 100 n",
    "太极实业 100 n", "易点天下 100 n", "天娱数科 100 n", "中文在线 100 n",
    "昆仑万维 100 n", "蓝色光标 100 n", "用友网络 100 n", "传智教育 100 n",
    "海康威视 100 n", "卧龙电驱 100 n", "浙文互联 100 n", "美利云 100 n",
    "普联软件 100 n", "长鑫存储 100 n", "长江存储 100 n",
    "人工智能 100 n", "机器学习 100 n", "大模型 100 n", "AI应用 100 n",
    "AI智能体 100 n", "智能体 100 n", "机器人 100 n", "人形机器人 100 n",
    "半导体 100 n", "芯片 100 n", "光刻机 100 n", "存储芯片 100 n",
    "新能源 100 n", "锂电池 100 n", "光伏 100 n", "固态电池 100 n",
    "券商 100 n", "互联网金融 100 n", "银行 100 n", "白酒 100 n",
    "消费 100 n", "医药 100 n", "创新药 100 n", "医疗器械 100 n",
    "军工 100 n", "航天 100 n", "低空经济 100 n", "数字经济 100 n",
    "中特估 100 n", "牛市 100 n", "熊市 100 n", "大盘 100 n",
    "涨停 100 n", "跌停 100 n", "龙头 100 n", "主升浪 100 n",
    "机构 100 n", "游资 100 n", "散户 100 n", "北向资金 100 n",
    "融资融券 100 n", "成交量 100 n", "市盈率 100 n", "市净率 100 n",
    "基本面 100 n", "技术面 100 n", "财报 100 n", "业绩 100 n",
    "利好 100 n", "利空 100 n", "超预期 100 n", "不及预期 100 n",
    "低估 100 n", "抄底 100 n", "反弹 100 n", "回调 100 n",
    "微软 100 n", "英伟达 100 n", "特斯拉 100 n", "苹果 100 n",
    "纳斯达克 100 n", "标普500 100 n", "美联储 100 n", "加息 100 n",
    "降息 100 n", "通胀 100 n", "原油 100 n", "黄金 100 n",
]


def _ensure_dict():
    if not os.path.exists(DICT_PATH):
        try:
            with open(DICT_PATH, "w", encoding="utf-8") as f:
                f.write("\n".join(FINANCE_WORDS))
        except OSError:
            pass
    try:
        jieba.load_userdict(DICT_PATH)
    except Exception:
        pass


_ensure_dict()

# 财经/通用停用词（不参与关键词统计）——大幅扩充
STOPWORDS = set("""
的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己
这 那 么 吧 啊 呢 吗 还 又 再 把 被 让 向 从 与 及 或 而 但 并 等 对 于 之 其 此 如 所 因 为 以
这个 那个 我们 你们 他们 咱们 大家 什么 怎么 为什么 时候 现在 今天 明天 昨天 已经 可以 应该
股票 股市 股价 板块 市场 行情 走势 资金 主力 机构 散户 大盘 指数 个股 上市 公司 涨停 跌停
今天 明天 昨天 目前 现在 已经 正在 继续 开始 出现 觉得 感觉 认为 知道 了解 希望 可能 应该
一个 一些 有点 非常 特别 真的 其实 只是 但是 不过 所以 因为 如果 虽然 然后 还有 或者 就是
上涨 下跌 涨幅 跌幅 涨跌 收盘 开盘 成交 放量 缩量 反弹 回调 震荡 调整 突破 支撑 压力
问题 情况 消息 新闻 资讯 报告 分析 观点 看法 评论 回复 楼主 各位 朋友 兄弟 老师
不是 不会 没有 不能 不敢 不要 不得 不可 无需 未必 并不
%% % 元 万 亿 块 钱 点 个 只 次 天 年 月 日
A B C D E F G H I J K L M N O P Q R S T U V W X Y Z
更多 更大 更快 更 最 很 太 极 非常 特别 十分 相当 比较 稍微 有点 有些
我们 你们 他们 咱们 自己 大家 这个 那个 这些 那些 这里 那里 这样 那样 怎么 怎样 什么
是否 能否 是否 不是 就是 而是 还是 只是 但是 可是 然而 于是 因此 所以 因为 由于 如果 假如
已经 曾经 正在 即将 将要 就会 就要 都会 可能 也许 大概 大约 差不多 似乎 好像
进行 通过 关于 对于 根据 按照 作为 成为 处于 属于 具有 拥有 存在 出现 发生 进行 产生
部分 方面 情况 时候 时间 地方 问题 事情 东西 人们 大家 方面 程度 水平 方式 方法 手段
接着 然后 后来 最后 首先 其次 再次 另外 此外 同时 以及 并且 而且 或者 要么
看到 知道 觉得 感觉 认为 以为 希望 期望 打算 准备 开始 继续 结束 完成 需要 应该 必须
不要 不用 不必 不能 不会 无法 难以 未必 并不 没有 没啥
今日 昨日 明日 本周 上周 下周 本月 上月 今年 去年 明年 上午 下午 晚上 早上
现在 目前 当前 近期 近期 未来 以后 之前 之后 期间 期间 阶段 过程
表示 显示 表明 说明 指出 强调 提到 提及 报道 称 称 称
投资者 投资 股民 基民 网友 用户 读者
技术 分析 走势 趋势 方向 区间 位置 层面 角度 视角 维度
公司 集团 股份 有限 控股 子公司 母公司
主要 重要 重大 关键 核心 基本 一般 普通 常见 典型
持续 不断 逐渐 逐步 慢慢 快速 迅速 立即 马上
市场 大盘 指数 板块 个股 股票 股市 股价 行情 走势 资金 主力
""".split())

# 停用词补充（追加式，避免上面遗漏）
EXTRA_STOPWORDS = """
回复 转发 点赞 收藏 关注 分享 评论 讨论 标题 正文 内容 原文 摘要 来源
东方财富 雪球 股吧 帖子 楼层 沙发 板凳 顶 支持 反对 路过 飘过
各位 大家 大佬 大神 高手 新手 小白 菜鸟 韭菜
老师 楼主 博主 作者 小编 记者 编辑
请问 谢谢 感谢 抱歉 不好意思 麻烦 请教 求教
其实 实际 事实 真相 本质 本质 究竟 到底 毕竟 终究
大概 基本 基本 大约 左右 上下 前后 之间 以内 以上 以下
认为 觉得 感觉 感到 表示 显示 表明 说明 指出 提到 提及
可能 或许 也许 大概 应该 必须 需要 能够 可以
今天 昨天 明天 现在 目前 当前 近期 未来 以后 之前 之后
时候 时间 现在 目前 当前 问题 情况 事情 东西
不是 没有 不会 不能 不要 不用 不必 无法 难以 未必
就是 而是 还是 只是 但是 可是 然而 于是 因此 所以 因为 由于
如果 假如 虽然 尽管 即使 无论 不管
一个 一些 一点 一次 一个 一只 一只 一种 一项 一家 一位 一天 一年
A股 B股 H股 美股 港股 科创板 创业板 主板 中小板
新三板 北交所 上交所 深交所 证监会 交易所
""".split()
STOPWORDS.update(EXTRA_STOPWORDS)

# 股票代码模式
CODE_RE = re.compile(r"\$?([A-Z]{2})?(\d{6})")
VALID_CODE_PREFIX = ("60", "00", "30", "68", "83", "87", "43", "92", "20")

# 词性白名单：只保留这些词性的词作为关键词（名词/动词/形容词/专名）
ALLOWED_POS = {"n", "nr", "ns", "nt", "nz", "nrt", "vn", "v", "an", "a", "eng", "x"}


def extract_keywords(texts: list[str], top_n: int = 10) -> list[dict]:
    """从帖子文本列表提取 top N 关键词
    返回 [{word, count, type}]，type: word=普通词, code=股票代码
    """
    counter = Counter()
    for text in texts:
        if not text:
            continue
        # 先提股票代码
        for m in CODE_RE.finditer(text):
            code = m.group(2)
            if code.startswith(VALID_CODE_PREFIX):
                counter[("code", code)] += 1
        # 分词 + 词性过滤
        cleaned = re.sub(r"https?://\S+|www\.\S+", "", text)
        for w, pos in jieba.posseg.cut(cleaned):
            w = w.strip()
            if len(w) < 2 or len(w) > 8:
                continue
            if w in STOPWORDS:
                continue
            if pos not in ALLOWED_POS:
                continue
            if re.fullmatch(r"[\d\W_]+", w):
                continue
            if w.isdigit():
                continue
            # 过滤 SZ600519 / SH600519 / 600519.SH 这类代码噪声
            if re.fullmatch(r"(?:SH|SZ|BJ)?\d{6}", w):
                continue
            counter[("word", w)] += 1

    items = []
    for (typ, word), count in counter.most_common(top_n * 4):
        items.append({"word": word, "count": count, "type": typ})

    words = [i for i in items if i["type"] == "word"][:top_n]
    codes = [i for i in items if i["type"] == "code"][:top_n]
    return words + codes


def extract_from_posts(posts: list[dict], top_n: int = 10) -> list[dict]:
    """从帖子列表提取关键词（取标题+文本）"""
    texts = []
    for p in posts:
        t = (p.get("title") or "") + " " + (p.get("text") or "")
        if t.strip():
            texts.append(t)
    return extract_keywords(texts, top_n=top_n)
