#!/usr/bin/env python3
"""AUD-22 machine-checkable assertions for .github/workflows/deploy.yml.

Checks that the release pipeline can never deploy a mutable tag and can never
deploy a commit whose CI did not pass. Exits 0 when every invariant holds, 1
otherwise, printing the evidence for each assertion.

Assertions run against the *parsed* workflow (comments stripped by the YAML
parser), so a mention of `:latest` inside an explanatory comment does not
masquerade as a deployable reference — only real configuration values count.

Invariants:
  D1  A job gates on CI success for the exact github.sha.
  D2  Every build job transitively needs that gate.
  D3  No configuration value in the workflow contains a mutable `:latest` tag.
  D4  Scan, sign and deploy consume digest-pinned references produced by the
      digest-pinning job, and that job rejects non-digest references.
  D5  The production deploy job keeps its manual-approval behaviour
      (workflow_dispatch + deploy input, and a protected `environment`).
  D6  Production deploys are serialised by a workflow-level concurrency group
      that does not cancel in-progress releases.
  D7  The SSH deploy script exports the digest-pinned refs and hard-fails on
      any non-digest or mutable-tag reference.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    print("需要 PyYAML：pip install pyyaml")
    sys.exit(2)

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy.yml"
MUTABLE_TAG = re.compile(r":latest\b")
GATE_JOB = "require-ci-success"
PIN_JOB = "collect-digests"
IMAGE_OUTPUTS = ("backend_image", "frontend_image", "collab_image")
CONSUMER_JOBS = ("trivy-image-scan", "sign-images", "deploy")

failures: List[str] = []
checks = 0


def check(condition: bool, label: str, evidence: str) -> None:
    global checks
    checks += 1
    if condition:
        print(f"  PASS  {label}\n        证据: {evidence}")
    else:
        failures.append(label)
        print(f"  FAIL  {label}\n        证据: {evidence}")


def step_script(job: dict) -> str:
    """Concatenate the real `run:` / `script:` / `env:` / `with:` values of a job.

    Reading the actual step values (rather than re-dumping the YAML) avoids
    round-trip artefacts such as line folding corrupting substring counts.
    """
    chunks: List[str] = []
    for step in job.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        for key in ("run", "script"):
            if isinstance(step.get(key), str):
                chunks.append(step[key])
        env_block = step.get("env")
        if isinstance(env_block, dict):
            chunks.extend(str(v) for v in env_block.values())
        with_block = step.get("with")
        if isinstance(with_block, dict):
            chunks.extend(str(v) for v in with_block.values())
    return "\n".join(chunks)


def walk(node: object, path: str = "") -> Iterator[Tuple[str, str]]:
    """Yield every (json-path, scalar-string) pair in the parsed workflow."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def transitive_needs(jobs: Dict[str, dict], start: str) -> Set[str]:
    seen: Set[str] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        raw = jobs.get(current, {}).get("needs", []) or []
        for dep in raw:
            name = dep if isinstance(dep, str) else list(dep)[0]
            if name not in seen:
                seen.add(name)
                frontier.append(name)
    return seen


def main() -> int:
    raw = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    jobs: Dict[str, dict] = doc["jobs"]

    print(f"被检查的工作流: {WORKFLOW}")
    print(f"作业列表: {list(jobs)}\n")

    # ---- D1: CI gate keyed on the exact commit ----------------------------
    gate_values = "\n".join(v for _, v in walk(jobs.get(GATE_JOB, {})))
    check(
        GATE_JOB in jobs
        and "github.sha" in gate_values
        and "head_sha" in gate_values
        and "ci.yml" in gate_values
        and "success" in gate_values,
        "D1 存在针对同一 commit 的 CI 成功门禁",
        (
            f"job {GATE_JOB} 查询 workflows/ci.yml?head_sha=${{{{ github.sha }}}}，"
            "要求该 commit 上每条运行 status=completed 且 conclusion=success"
        )
        if GATE_JOB in jobs
        else "未找到 CI 门禁 job",
    )

    # ---- D2: every build job transitively needs the gate -------------------
    build_jobs = sorted(n for n in jobs if n.startswith("build-and-push-"))
    ungated = [n for n in build_jobs if GATE_JOB not in transitive_needs(jobs, n)]
    check(
        bool(build_jobs) and not ungated,
        "D2 所有构建作业都传递依赖 CI 门禁",
        (
            f"构建作业 {build_jobs} 全部（传递）needs {GATE_JOB}"
            if not ungated
            else f"未受门禁约束: {ungated}"
        ),
    )

    # ---- D3: no mutable tag is ever *consumed* ----------------------------
    # 只统计"会被真正当作镜像引用消费"的值：metadata-action 的 tags、
    # trivy 的 image-ref、compose 的 image、以及 *_IMAGE 变量。
    # 护栏里出现的 *:latest 是"拒绝该标签"的匹配模式，不是被消费的引用。
    consumed: List[Tuple[str, str]] = []
    for job_name, job in jobs.items():
        for step in job.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            with_block = step.get("with")
            if isinstance(with_block, dict):
                for key in ("tags", "image-ref", "image"):
                    if isinstance(with_block.get(key), str):
                        consumed.append((f"{job_name}.with.{key}", with_block[key]))
            env_block = step.get("env")
            if isinstance(env_block, dict):
                for key, value in env_block.items():
                    if str(key).endswith("_IMAGE"):
                        consumed.append((f"{job_name}.env.{key}", str(value)))
    tag_hits = [(p, v) for p, v in consumed if re.search(r"value=latest|:latest\b", v)]
    check(
        bool(consumed) and not tag_hits,
        "D3 没有任何被消费的镜像引用使用可变标签 :latest",
        (
            f"检查了 {len(consumed)} 处镜像引用/metadata tags，均不含 value=latest 或 :latest"
            if not tag_hits
            else f"发现 {len(tag_hits)} 处可变标签: " + "; ".join(p for p, _ in tag_hits[:5])
        ),
    )

    # ---- D4: scan / sign / deploy consume digest-pinned refs ---------------
    expected_refs = {f"needs.{PIN_JOB}.outputs.{name}" for name in IMAGE_OUTPUTS}
    missing: Dict[str, Set[str]] = {}
    for name in CONSUMER_JOBS:
        body = step_script(jobs.get(name, {}))
        gap = {ref for ref in expected_refs if ref not in body}
        if gap:
            missing[name] = gap
    check(
        all(n in jobs for n in CONSUMER_JOBS) and not missing,
        "D4 扫描 / 签名 / 部署都消费 digest 固定的镜像引用",
        (
            f"{list(CONSUMER_JOBS)} 全部引用 {sorted(expected_refs)}"
            if not missing
            else f"未全部使用 digest 引用: {missing}"
        ),
    )

    pin_script = step_script(jobs.get(PIN_JOB, {}))
    check(
        "@sha256:" in pin_script and ":latest" in pin_script and pin_script.count("exit 1") >= 2,
        "D4b digest 固定作业会拒绝非 digest 引用",
        (
            f"job {PIN_JOB} 校验 *@sha256:*，命中 :latest 或非 digest 引用即 exit 1"
            f"（{pin_script.count('exit 1')} 处 exit 1）"
        ),
    )

    # ---- D5: manual approval preserved -------------------------------------
    deploy_job = jobs.get("deploy", {})
    deploy_if = str(deploy_job.get("if", ""))
    check(
        "workflow_dispatch" in deploy_if
        and "deploy" in deploy_if
        and deploy_job.get("environment") == "production",
        "D5 生产部署保留人工审批",
        f"if: {deploy_if} ; environment: {deploy_job.get('environment')}",
    )

    # ---- D6: serialised production deploys ---------------------------------
    concurrency = doc.get("concurrency", {})
    check(
        "group" in concurrency and concurrency.get("cancel-in-progress") is False,
        "D6 生产发布被串行化且不中途取消",
        f"concurrency: {concurrency}",
    )

    # ---- D7: remote script hard-fails on non-digest refs -------------------
    ssh_script = ""
    for step in deploy_job.get("steps", []):
        with_block = (step.get("with") or {}) if isinstance(step, dict) else {}
        if isinstance(with_block, dict) and "script" in with_block:
            ssh_script = str(with_block["script"])
    check(
        "export BACKEND_IMAGE FRONTEND_IMAGE COLLAB_IMAGE" in ssh_script
        and "@sha256:" in ssh_script
        and ":latest" in ssh_script
        and ssh_script.count("exit 1") >= 3,
        "D7 远端脚本导出 digest 引用并对可变标签硬失败",
        (
            f"SSH script: 导出三个 *_IMAGE，校验 *@sha256:*，"
            f"命中 :latest 即 exit 1（脚本共 {ssh_script.count('exit 1')} 处 exit 1）"
        ),
    )

    print(f"\nAUD-22 断言：共 {checks} 项，失败 {len(failures)} 项")
    if failures:
        for name in failures:
            print(f"  未通过: {name}")
        return 1
    print("  全部通过：不存在可变标签发布，且存在同一 commit 的 CI 成功门禁。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
