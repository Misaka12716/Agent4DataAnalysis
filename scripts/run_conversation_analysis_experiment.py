#!/usr/bin/env python3
"""对话数据分析实验：对比单文件 vs 多文件组合下 /run-analysis 输出效果。

不纳入默认 pytest。需运行中的后端 + 有效 Bearer Token + 已导入的 case 素材。

用法见 tests/fixtures/conversation_analysis/README.md 与 docs/Tests.md §6。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import requests
except ImportError as exc:  # pragma: no cover
    print("需要 requests：pip install requests", file=sys.stderr)
    raise SystemExit(1) from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests/fixtures/conversation_analysis/cases"
DEFAULT_PROMPT = ROOT / "tests/fixtures/conversation_analysis/prompts/default.txt"
DEFAULT_RESULTS = ROOT / "tests/fixtures/conversation_analysis/results"
DEFAULT_BASE_URL = os.environ.get("BASE_URL", "http://localhost:52716").rstrip("/")


@dataclass
class CaseSpec:
    scenario: str  # single | multi
    case_id: str
    name: str
    case_dir: Path
    files: List[str]
    prompt: str


@dataclass
class CaseResult:
    scenario: str
    case_id: str
    name: str
    files: List[str]
    session_id: Optional[str] = None
    ok: bool = False
    error: Optional[str] = None
    elapsed_sec: Optional[float] = None
    streaming_ended: bool = False
    worker_success: Optional[bool] = None
    report_chars: int = 0
    report_preview: str = ""
    event_count: int = 0
    out_dir: Optional[str] = None


def _load_default_prompt(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"默认 prompt 不存在: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"默认 prompt 为空: {path}")
    return text


def discover_cases(
    cases_dir: Path,
    default_prompt: str,
    only: str,
) -> List[CaseSpec]:
    specs: List[CaseSpec] = []
    scenarios = ("single", "multi") if only == "all" else (only,)
    for scenario in scenarios:
        root = cases_dir / scenario
        if not root.is_dir():
            continue
        for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            meta_path = case_dir / "meta.json"
            if not meta_path.is_file():
                example = case_dir / "meta.json.example"
                if example.is_file():
                    print(
                        f"[skip] {scenario}/{case_dir.name}: 仅有 meta.json.example，"
                        "请复制为 meta.json 并放入数据文件后再跑",
                        file=sys.stderr,
                    )
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            files = meta.get("files") or []
            if not isinstance(files, list) or not files:
                raise ValueError(f"{meta_path}: files 必须为非空列表")
            if scenario == "single" and len(files) != 1:
                print(
                    f"[warn] {scenario}/{case_dir.name}: single 场景建议仅 1 个文件，"
                    f"当前 files={files}",
                    file=sys.stderr,
                )
            if scenario == "multi" and len(files) < 2:
                print(
                    f"[warn] {scenario}/{case_dir.name}: multi 场景建议 ≥2 个文件，"
                    f"当前 files={files}",
                    file=sys.stderr,
                )
            prompt = meta.get("prompt")
            if not prompt:
                prompt = default_prompt
            specs.append(
                CaseSpec(
                    scenario=scenario,
                    case_id=case_dir.name,
                    name=str(meta.get("name") or case_dir.name),
                    case_dir=case_dir,
                    files=[str(f) for f in files],
                    prompt=str(prompt).strip(),
                )
            )
    return specs


def validate_case(spec: CaseSpec) -> List[str]:
    errors: List[str] = []
    if not spec.prompt:
        errors.append("prompt 为空")
    for name in spec.files:
        path = spec.case_dir / name
        if not path.is_file():
            errors.append(f"缺少文件: {name}")
        elif ".." in Path(name).parts or Path(name).is_absolute():
            errors.append(f"非法相对路径: {name}")
    return errors


class ApiClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def create_session(self, project_id: Optional[int] = None) -> str:
        body: Dict[str, Any] = {}
        if project_id is not None:
            body["project_id"] = project_id
        r = self.session.post(
            f"{self.base_url}/session/create",
            json=body,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        sid = (data.get("data") or {}).get("session_id")
        if not sid:
            raise RuntimeError(f"创建会话失败: {data}")
        return str(sid)

    def upload_file(self, session_id: str, file_path: Path) -> None:
        with file_path.open("rb") as fh:
            r = self.session.post(
                f"{self.base_url}/session/upload-excel",
                data={"session_id": session_id},
                files={"file": (file_path.name, fh)},
                timeout=max(self.timeout, 120.0),
            )
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") not in (None, "success", "ok"):
            # 部分接口只返回 data；仅在明确失败字段时抛错
            if payload.get("error") or payload.get("code"):
                raise RuntimeError(f"上传失败 {file_path.name}: {payload}")

    def run_analysis_sse(
        self, session_id: str, input_data: str, analysis_timeout: float
    ) -> Tuple[List[Dict[str, Any]], str]:
        """消费 SSE，返回 (events, report_text)。"""
        events: List[Dict[str, Any]] = []
        report_parts: List[str] = []
        with self.session.post(
            f"{self.base_url}/run-analysis",
            json={"session_id": session_id, "input_data": input_data},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=analysis_timeout,
        ) as r:
            r.raise_for_status()
            for raw in _iter_sse_data_lines(r.iter_lines(decode_unicode=True)):
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    events.append({"type": "raw", "content": raw})
                    continue
                events.append(evt)
                et = evt.get("type")
                if et == "report_chunk":
                    content = evt.get("content")
                    if content is None and isinstance(evt.get("data"), dict):
                        content = evt["data"].get("content")
                    if content:
                        report_parts.append(str(content))
                if et in ("streaming_ended", "streaming_error", "error"):
                    break
        return events, "".join(report_parts)

    def snapshot(self, session_id: str) -> Any:
        r = self.session.get(
            f"{self.base_url}/session/snapshot",
            params={"session_id": session_id},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()


def _iter_sse_data_lines(lines: Iterable[Optional[str]]) -> Iterable[str]:
    buf: List[str] = []
    for line in lines:
        if line is None:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
            continue
        if line == "" and buf:
            yield "\n".join(buf)
            buf = []
    if buf:
        yield "\n".join(buf)


def _worker_success_from_events(events: List[Dict[str, Any]]) -> Optional[bool]:
    last: Optional[bool] = None
    for evt in events:
        if evt.get("type") != "worker":
            continue
        content = evt.get("content")
        if isinstance(content, dict) and "success" in content:
            last = bool(content["success"])
        elif isinstance(evt.get("success"), bool):
            last = bool(evt["success"])
    return last


def _streaming_ended(events: List[Dict[str, Any]]) -> bool:
    return any(e.get("type") == "streaming_ended" for e in events)


def run_case(
    client: ApiClient,
    spec: CaseSpec,
    run_dir: Path,
    project_id: Optional[int],
    analysis_timeout: float,
) -> CaseResult:
    result = CaseResult(
        scenario=spec.scenario,
        case_id=spec.case_id,
        name=spec.name,
        files=list(spec.files),
    )
    out = run_dir / spec.scenario / spec.case_id
    out.mkdir(parents=True, exist_ok=True)
    result.out_dir = str(out.relative_to(run_dir))

    t0 = time.perf_counter()
    try:
        sid = client.create_session(project_id=project_id)
        result.session_id = sid
        for name in spec.files:
            client.upload_file(sid, spec.case_dir / name)
        events, report = client.run_analysis_sse(
            sid, spec.prompt, analysis_timeout=analysis_timeout
        )
        result.elapsed_sec = round(time.perf_counter() - t0, 3)
        result.event_count = len(events)
        result.streaming_ended = _streaming_ended(events)
        result.worker_success = _worker_success_from_events(events)
        result.report_chars = len(report)
        result.report_preview = report[:500].replace("\n", " ")
        result.ok = result.streaming_ended and not any(
            e.get("type") in ("streaming_error", "error") for e in events
        )

        with (out / "events.jsonl").open("w", encoding="utf-8") as fh:
            for evt in events:
                fh.write(json.dumps(evt, ensure_ascii=False) + "\n")
        (out / "report.md").write_text(report or "(empty report)\n", encoding="utf-8")
        (out / "meta_used.json").write_text(
            json.dumps(
                {
                    "name": spec.name,
                    "files": spec.files,
                    "prompt": spec.prompt,
                    "session_id": sid,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            snap = client.snapshot(sid)
            (out / "snapshot.json").write_text(
                json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as snap_exc:  # noqa: BLE001
            (out / "snapshot_error.txt").write_text(str(snap_exc), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.elapsed_sec = round(time.perf_counter() - t0, 3)
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        (out / "error.txt").write_text(result.error + "\n", encoding="utf-8")
    return result


def write_summary(run_dir: Path, results: List[CaseResult], base_url: str, run_id: str) -> None:
    single = [r for r in results if r.scenario == "single"]
    multi = [r for r in results if r.scenario == "multi"]

    lines: List[str] = [
        f"# 对话数据分析实验对比 — `{run_id}`",
        "",
        f"- base_url: `{base_url}`",
        f"- cases: {len(results)}（single={len(single)}, multi={len(multi)}）",
        "",
        "## 汇总表",
        "",
        "| 场景 | case_id | ok | 耗时(s) | streaming_ended | worker_success | 报告字数 | 文件 |",
        "|------|---------|----|---------|-----------------|----------------|----------|------|",
    ]
    for r in results:
        lines.append(
            "| {scenario} | {case_id} | {ok} | {elapsed} | {ended} | {ws} | {chars} | {files} |".format(
                scenario=r.scenario,
                case_id=r.case_id,
                ok="yes" if r.ok else "no",
                elapsed=r.elapsed_sec if r.elapsed_sec is not None else "-",
                ended="yes" if r.streaming_ended else "no",
                ws="-" if r.worker_success is None else ("yes" if r.worker_success else "no"),
                chars=r.report_chars,
                files=", ".join(r.files),
            )
        )

    lines.extend(["", "## 单文件 vs 多文件", ""])
    if single and multi:
        s_ok = sum(1 for r in single if r.ok)
        m_ok = sum(1 for r in multi if r.ok)
        s_avg = _avg([r.elapsed_sec for r in single if r.elapsed_sec is not None])
        m_avg = _avg([r.elapsed_sec for r in multi if r.elapsed_sec is not None])
        s_chars = _avg([float(r.report_chars) for r in single])
        m_chars = _avg([float(r.report_chars) for r in multi])
        lines.extend(
            [
                f"- 单文件：成功 {s_ok}/{len(single)}，平均耗时 {s_avg}s，平均报告字数 {s_chars}",
                f"- 多文件：成功 {m_ok}/{len(multi)}，平均耗时 {m_avg}s，平均报告字数 {m_chars}",
                "",
                "请结合各 case 目录下的 `report.md` / `events.jsonl` 人工对比结论质量与是否正确利用多表信息。",
            ]
        )
    else:
        lines.append("（本次未同时跑到 single 与 multi 两组，无法做并排汇总。）")

    lines.extend(["", "## 报告预览", ""])
    for r in results:
        lines.append(f"### [{r.scenario}] {r.case_id} — {r.name}")
        if r.error:
            lines.append(f"- error: `{r.error}`")
        lines.append(f"- preview: {r.report_preview or '(empty)'}")
        lines.append("")

    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "base_url": base_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "results": [asdict(r) for r in results],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _avg(vals: List[float]) -> str:
    if not vals:
        return "-"
    return f"{sum(vals) / len(vals):.2f}"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="对话数据分析单/多文件对比实验")
    p.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"后端地址（默认 env BASE_URL 或 {DEFAULT_BASE_URL}）",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("TOKEN", ""),
        help="Bearer access_token（也可用环境变量 TOKEN）",
    )
    p.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES)
    p.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    p.add_argument(
        "--only",
        choices=("all", "single", "multi"),
        default="all",
        help="只跑某一类场景",
    )
    p.add_argument("--project-id", type=int, default=None, help="可选；省略则用个人默认项目")
    p.add_argument(
        "--analysis-timeout",
        type=float,
        default=float(os.environ.get("ANALYSIS_TIMEOUT", "900")),
        help="单次 /run-analysis SSE 超时秒数（默认 900）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验 cases/meta/文件，不调 API",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        default_prompt = _load_default_prompt(args.prompt_file)
    except (OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    if not args.cases_dir.is_dir():
        print(
            f"[error] cases 目录不存在: {args.cases_dir}\n"
            "请按 tests/fixtures/conversation_analysis/README.md 导入素材。",
            file=sys.stderr,
        )
        return 2

    specs = discover_cases(args.cases_dir, default_prompt, args.only)
    if not specs:
        print(
            f"[error] 未发现可运行 case（需 meta.json + 数据文件）。\n"
            f"目录: {args.cases_dir}\n"
            "可将 meta.json.example 复制为 meta.json 并放入 files 所列文件。",
            file=sys.stderr,
        )
        return 2

    all_errors: List[str] = []
    for spec in specs:
        errs = validate_case(spec)
        for e in errs:
            all_errors.append(f"{spec.scenario}/{spec.case_id}: {e}")

    print(f"[info] 发现 {len(specs)} 个 case")
    for spec in specs:
        print(f"  - [{spec.scenario}] {spec.case_id}: files={spec.files}")

    if all_errors:
        print("[error] 素材校验失败:", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("[ok] dry-run 通过，未调用 API")
        return 0

    if not args.token:
        print(
            "[error] 需要 --token 或环境变量 TOKEN（不纳入默认 pytest 的联调实验）",
            file=sys.stderr,
        )
        return 2

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    client = ApiClient(args.base_url, args.token)
    results: List[CaseResult] = []
    for spec in specs:
        print(f"[run] [{spec.scenario}] {spec.case_id} ...")
        res = run_case(
            client,
            spec,
            run_dir,
            project_id=args.project_id,
            analysis_timeout=args.analysis_timeout,
        )
        results.append(res)
        status = "ok" if res.ok else "FAIL"
        print(
            f"  -> {status} session={res.session_id} "
            f"elapsed={res.elapsed_sec}s report_chars={res.report_chars}"
            + (f" error={res.error}" if res.error else "")
        )

    write_summary(run_dir, results, args.base_url, run_id)
    print(f"[done] 结果目录: {run_dir}")
    print(f"       summary: {run_dir / 'summary.md'}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
