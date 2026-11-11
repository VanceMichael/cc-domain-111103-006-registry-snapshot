"""版本号比较工具。

版本号字符串不能按字典顺序决定新旧（例如字典序下 "1.10.0" < "1.9.0"）。
这里把版本拆成数字段与文本段分别比较：数字段按数值比较，文本段按字母序，
并处理常见的前置版本（pre-release）语义：1.0.0-alpha 早于 1.0.0。

这是对合成样例数据的实用比较器，不声称完整实现 PEP 440 或 semver。
"""

import re

_TOKEN = re.compile(r"\d+|[A-Za-z]+")


def _tokens(text):
    parts = []
    for tok in _TOKEN.findall(text):
        if tok.isdigit():
            # 数字标识符按数值比较，且排在文本标识符之前（与 semver 一致）。
            parts.append((0, int(tok), ""))
        else:
            parts.append((1, 0, tok.lower()))
    return tuple(parts)


def version_key(version):
    """返回可比较的键。拒绝空版本，绝不退化为整串字典序。"""
    if not isinstance(version, str) or not version.strip():
        raise ValueError("版本号不能为空")
    text = version.strip()
    if text.startswith(("v", "V")) and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    core, sep, pre = text.partition("-")
    core = core.split("+", 1)[0]  # 构建元数据不参与新旧比较
    key = _tokens(core)
    if not key:
        raise ValueError(f"版本号无法解析: {version!r}")
    # 没有前置段时附加较高位阶，使 1.0.0-alpha < 1.0.0。
    release_rank = (0,) if sep else (1,)
    return (key, release_rank, _tokens(pre) if sep else ())


def compare(left, right):
    a, b = version_key(left), version_key(right)
    return (a > b) - (a < b)


def latest(versions):
    """返回版本列表中“最新”的版本字符串；空列表返回 None。"""
    items = list(versions)
    if not items:
        return None
    return max(items, key=version_key)
