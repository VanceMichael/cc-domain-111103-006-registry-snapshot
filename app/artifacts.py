"""制品文件校验：只按长度和 sha256 摘要核对。

本模块读取文件字节流计算摘要，绝不导入、解压执行或运行包内任何代码。
用于核对本地缓存的制品是否仍与目录当前记录一致：缓存里的旧制品在
登记源替换制品后必须能被识别为过期。
"""

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def measure(path):
    """返回文件的 (长度, sha256)。只读字节，不执行内容。"""
    digest = hashlib.sha256()
    length = 0
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            length += len(chunk)
            digest.update(chunk)
    return length, digest.hexdigest()


def verify_artifact(path, expected_sha256=None, expected_length=None):
    """按长度与摘要校验制品文件，返回核对结果字典。"""
    length, sha256 = measure(path)
    length_ok = expected_length is None or length == expected_length
    digest_ok = expected_sha256 is None or sha256 == expected_sha256.lower()
    return {
        "path": str(path),
        "length": length,
        "sha256": sha256,
        "length_ok": length_ok,
        "digest_ok": digest_ok,
        "ok": length_ok and digest_ok,
    }


def check_cache(path, artifact):
    """对照目录当前制品记录检查缓存文件。

    返回 (是否仍是最新, 核对结果)。artifact 为目录视图中的 artifact 字段。
    """
    if not artifact:
        raise ValueError("目录中没有该版本的制品记录")
    result = verify_artifact(path, expected_sha256=artifact["sha256"],
                             expected_length=artifact.get("length"))
    result["current_sha256"] = artifact["sha256"]
    result["verdict"] = "current" if result["ok"] else "stale"
    return result["ok"], result
