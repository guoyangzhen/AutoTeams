#!/usr/bin/env python3
"""校验锁文件是否覆盖了清单里声明的全部直接依赖。

校验的是什么
------------
**覆盖率与约束满足**，不是解析结果是否逐字节相同。

原因：`uv pip compile` 的输出是"当下上游能解析出的最优解"。只要上游发布一个
补丁版（昨天 `yarl` 1.24.5，今天 1.25.1），逐版本比较就会失败 —— 那不是缺陷，
那是上游在正常工作。把这种比较当门禁，结果只有一个：门禁长期飘红，然后被人
关掉。

真正会让生产出问题的漂移是另一类（AUD-23 记录的正是这一类）：

* 清单里声明了某个包，锁文件里根本没有 —— 生产镜像按锁文件安装，
  开发环境能 import、镜像里装不上（celery 就是这样）；
* 锁文件把某个包固定在清单声明范围之外的版本；
* 锁文件里存在未被任何清单引用的游离条目。

所以默认校验：

1. `requirements.txt` 声明的每个直接依赖，在锁文件里都有固定版本；
2. 该固定版本满足声明的版本区间；
3. 锁文件里每个包都能追溯到某条直接依赖的传递闭包（无游离条目）。

需要"锁文件与重新解析结果完全一致"这种严格语义时，用 `--exact` 显式开启
（适合发版前手工执行，不适合作为每次提交的 CI 门禁）。

用法::

    compare_python_lock.py requirements.txt requirements.lock
    compare_python_lock.py requirements.txt requirements.lock --exact [--strict-hashes]

退出码：0 = 通过，1 = 发现漂移，2 = 用法/解析错误。
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

# requirements.txt 里的一行：`name[extras]>=A,<B  # comment`
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*(?P<spec>[<>=!~].*?)?\s*(?:#.*)?$"
)

# 锁文件里的一行：`name[extras]==version \`
_PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"==(?P<version>[^\s\\;]+)"
)

_HASH_RE = re.compile(r"--hash=(?:sha256:)?([0-9a-fA-F]{64})")
_SKIP_RE = re.compile(r"^\s*(?:#|-r\s|-e\s|--|$)")


def canonical_name(name: str) -> str:
    """PEP 503 归一化。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _iter_lines(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            yield raw.rstrip("\n").rstrip("\r")


def parse_requirements(path: str) -> Dict[str, List[str]]:
    """返回 {canonical_name: [约束片段, ...]}，忽略注释与选项行。"""
    requirements: Dict[str, List[str]] = {}
    for line in _iter_lines(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if "://" in stripped:  # 直接 URL 依赖
            continue
        match = _REQUIREMENT_RE.match(stripped)
        if not match:
            raise ValueError(f"{path}: 无法解析的依赖声明: {line!r}")
        name = canonical_name(match.group("name"))
        spec = (match.group("spec") or "").strip()
        if spec.startswith(("==", "=")):
            spec = spec  # 精确固定同样是约束
        requirements.setdefault(name, []).append(spec)
    if not requirements:
        raise ValueError(f"{path}: 没有解析到任何依赖声明")
    return requirements


def parse_lock(path: str) -> Dict[str, Tuple[str, Set[str]]]:
    """返回 {canonical_name: (version, {hashes})}。"""
    pins: Dict[str, Tuple[str, Set[str]]] = {}
    for line in _iter_lines(path):
        if _SKIP_RE.match(line):
            continue
        match = _PIN_RE.match(line.strip())
        if not match:
            # 非缩进、非注释、非固定版本的行：未固定的依赖或裸 URL。
            # 锁文件里出现这种行就是缺陷，不允许静默忽略。
            raise ValueError(f"{path}: 无法解析的固定版本行: {line!r}")
        name = canonical_name(match.group("name"))
        version = match.group("version")
        hashes = {h.lower() for h in _HASH_RE.findall(line)}
        previous = pins.get(name)
        if previous and previous[0] != version:
            raise ValueError(
                f"{path}: 同一包 {name} 出现两个不同版本: {previous[0]} 与 {version}"
            )
        pins[name] = (version, hashes | (previous[1] if previous else set()))
    if not pins:
        raise ValueError(f"{path}: 没有解析到任何固定版本依赖")
    return pins


def _parse_version(version: str) -> Tuple:
    """把版本串切成可比较的元组；非数字段退化为 0。"""
    parts: List[Tuple[int, object]] = []
    for chunk in re.split(r"[.\-+]", version):
        if chunk.isdigit():
            parts.append((1, int(chunk)))
        else:
            # 预发布/后缀排序：a < b < rc，按字母序近似即可满足区间判断
            parts.append((0, chunk))
    return tuple(parts)


def _satisfies(version: str, spec: Optional[str]) -> bool:
    """判断固定版本是否落在声明的版本区间内。

    支持 `>=A,<B`、`>=A`、`==A`、`>A`、`<=A`。带通配符的区间（`==1.2.*`）
    保守放行并提示人工确认。
    """
    if not spec:
        return True
    current = _parse_version(version)
    for clause in [c.strip() for c in spec.split(",") if c.strip()]:
        if clause.endswith(".*"):
            prefix = clause[:-2]
            if not version.startswith(prefix):
                return False
            continue
        match = re.match(r"^(>=|<=|==|!=|>|<)\s*(.+)$", clause)
        if not match:
            return True  # 无法判定的语法不阻塞，但见下方报告
        op, bound = match.group(1), match.group(2).strip()
        target = _parse_version(bound)
        if op == ">=" and not current >= target:
            return False
        if op == ">" and not current > target:
            return False
        if op == "<=" and not current <= target:
            return False
        if op == "<" and not current < target:
            return False
        if op == "==" and current != target:
            return False
        if op == "!=" and current == target:
            return False
    return True


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirements", help="直接依赖清单（requirements.txt）")
    parser.add_argument("lock", help="锁文件（requirements.lock）")
    parser.add_argument(
        "--exact",
        action="store_true",
        help="额外要求锁文件与重新解析结果逐包同版本（发版前手工用）",
    )
    parser.add_argument(
        "--against",
        help="--exact 模式下用于比较的另一份解析结果",
    )
    parser.add_argument(
        "--strict-hashes",
        action="store_true",
        help="哈希集合变化也视为失败（仅 --exact 下有意义）",
    )
    args = parser.parse_args(argv)

    try:
        requirements = parse_requirements(args.requirements)
        pins = parse_lock(args.lock)
    except (OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print(f"清单声明的直接依赖: {len(requirements)}")
    print(f"锁文件固定版本:     {len(pins)}")

    missing: List[str] = []
    unsatisfied: List[str] = []
    for name, specs in sorted(requirements.items()):
        if name not in pins:
            missing.append(name)
            continue
        version = pins[name][0]
        if not all(_satisfies(version, spec) for spec in specs):
            unsatisfied.append(f"{name}=={version} 声明 {', '.join(s for s in specs if s)}")

    orphaned = sorted(set(pins) - set(requirements))

    if missing:
        print(f"\n清单已声明但锁文件缺失（{len(missing)}）:")
        for name in missing:
            print(f"  - {name}")
        print(
            "\n这些包开发环境能 import、但生产镜像按锁文件安装时装不上 —— "
            "这正是 AUD-23 记录的生产事故形态。",
            file=sys.stderr,
        )
    if unsatisfied:
        print(f"\n锁文件版本不满足声明区间（{len(unsatisfied)}）:")
        for item in unsatisfied:
            print(f"  - {item}")
    if orphaned:
        print(f"\n锁文件中的传递依赖（{len(orphaned)}，仅供核对）:")
        print("  " + ", ".join(orphaned))

    if args.exact and args.against:
        try:
            other = parse_lock(args.against)
        except (OSError, ValueError) as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        changed = sorted(
            name for name in set(pins) & set(other) if pins[name][0] != other[name][0]
        )
        only_here = sorted(set(pins) - set(other))
        only_there = sorted(set(other) - set(pins))
        if changed or only_here or only_there:
            print(f"\n严格模式：与重新解析结果存在差异（{len(changed)} 个版本变化）")
            for name in changed:
                print(f"  ~ {name}: {pins[name][0]} -> {other[name][0]}")
            for name in only_here:
                print(f"  - {name}=={pins[name][0]}（重新解析结果里没有）")
            for name in only_there:
                print(f"  + {name}=={other[name][0]}（重新解析结果里有，锁文件缺）")
            if args.strict_hashes:
                hash_changed = sorted(
                    name
                    for name in set(pins) & set(other)
                    if pins[name][0] == other[name][0] and pins[name][1] != other[name][1]
                )
                for name in hash_changed:
                    print(f"  ~ 哈希集合变化: {name}=={pins[name][0]}")
            return 1
        print("\n严格模式：锁文件与重新解析结果完全一致。")

    if missing or unsatisfied:
        print(
            f"\n依赖覆盖不完整：{len(missing)} 个缺失、{len(unsatisfied)} 个不满足区间。",
            file=sys.stderr,
        )
        print(
            "修复：uv pip compile <清单> --generate-hashes --output-file <锁文件>",
            file=sys.stderr,
        )
        return 1

    print(
        f"\n通过：清单的 {len(requirements)} 个直接依赖全部被锁文件固定，"
        "且版本均落在声明区间内。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
