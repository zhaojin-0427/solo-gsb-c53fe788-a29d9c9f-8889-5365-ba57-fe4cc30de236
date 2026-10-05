"""交付字幕核验：回传字幕与项目生效时间轴的逐条对照。

对照规则（页面与接口一致）：
- 项目侧取当前生效时间轴：已采纳 → 提案 → 原值（与 SRT 导出同一条，
  由调用方逐条算好后传入）；
- 交付稿可能重编号、插删行、重复文案，因此不按 cue_id / 行号配对，
  只在「可见文本完全相同」的条目之间建立对应，且对应保持双方顺序；
- 对应择优：先使匹配对数最多，再使各对起止偏差之和最小
  （每对偏差 = |起点差| + |终点差|）；
- 若多组等优对应会改变某行结论，该行记「待确认」，不强判缺失或时间不符；
- 匹配对仅在起止毫秒均相同时记「通过」，其余记「时间不符」；
- 未匹配的项目条目记「缺失」，未匹配的交付条目记「新增」；
  拆并行不自动合并（严格 1:1 对应，不把多行并作一行）。

实现：把「匹配对数最多、偏差和最小」编码为单个整数分值
score = 对数 * C − 偏差和（C 大于任何可达偏差和，故最大化 score 等价于
字典序择优），用 LCS 型网格 DP 求最优分值 S。前缀表 F 全量保存，
后缀表 B 自底向上逐行扫描，在扫描中判定每条线在所有最优对应里的
可能结论：
- 对 (i,j) 出现在某个最优对应 ⟺ F[i][j] + w(i,j) + B[i+1][j+1] == S；
- 项目行 i 在某个最优对应中未匹配 ⟺ ∃j: F[i][j] + B[i+1][j] == S；
- 交付行 j 在某个最优对应中未匹配 ⟺ ∃i: F[i][j] + B[i][j+1] == S。
行的可能结论集合大于一个时即「待确认」。核验为纯计算，不落库。
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass

# 结论取值
PASS = "pass"                    # 通过（匹配且起止毫秒均相同）
TIME_MISMATCH = "time_mismatch"  # 时间不符（匹配但起止毫秒不同）
MISSING = "missing"              # 缺失（项目条目未匹配）
ADDED = "added"                  # 新增（交付条目未匹配）
PENDING = "pending"              # 待确认（等优对应会改变该行结论）

_NEG = -(1 << 60)                # 负无穷（分值下界远离 int64 溢出区）
_MAX_CANDIDATES = 25             # 每行最多返回的候选对应数（仅供展示）
_MAX_CELLS = 25_000_000          # DP 网格规模上限（约 200MB 前缀表）


@dataclass
class CheckLine:
    """参与核验的一行：可见文本 + 起止毫秒（项目侧为生效时间轴值）。"""
    text: str
    start_ms: int
    end_ms: int


def _fwd_row(prev: array, wcols: dict[int, int]) -> array:
    """F[i] 行：F[i][j] = max(F[i-1][j], F[i][j-1], F[i-1][j-1] + w(i,j))。
    展开行内递推：F[i][j] = max( max_{k≤j} F[i-1][k],
    max_{k≤j} (F[i-1][k-1] + w(k)) )，wcols 为 {列j(1基): 权重}。"""
    n1 = len(prev)
    cur = array("q", bytes(8 * n1))
    run_a = prev[0]          # max_{k≤j} prev[k]
    run_b = _NEG             # max_{k≤j} prev[k-1] + w(k)
    cur[0] = run_a
    for j in range(1, n1):
        pj = prev[j]
        if pj > run_a:
            run_a = pj
        w = wcols.get(j)
        if w is not None:
            v = prev[j - 1] + w
            if v > run_b:
                run_b = v
        cur[j] = run_a if run_a >= run_b else run_b
    return cur


def _bwd_row(bnext: array, wcols: dict[int, int]) -> array:
    """B[i] 行（由 B[i+1] 计算）：B[i][j] = max(B[i+1][j], B[i][j+1],
    B[i+1][j+1] + w(i+1, j+1))。自右向左展开同 _fwd_row。"""
    n1 = len(bnext)
    cur = array("q", bytes(8 * n1))
    run_a = bnext[n1 - 1]    # max_{k≥j} bnext[k]
    run_b = _NEG             # max_{k≥j} bnext[k+1] + w(k+1)
    cur[n1 - 1] = run_a
    for j in range(n1 - 2, -1, -1):
        pj = bnext[j]
        if pj > run_a:
            run_a = pj
        w = wcols.get(j + 1)
        if w is not None:
            v = bnext[j + 1] + w
            if v > run_b:
                run_b = v
        cur[j] = run_a if run_a >= run_b else run_b
    return cur


def _resolve(possible: set[str]) -> str:
    """可能结论唯一时取该结论，否则待确认。"""
    if len(possible) == 1:
        return next(iter(possible))
    return PENDING


def verify_delivery(
    project_lines: list[CheckLine],
    delivery_lines: list[CheckLine],
) -> dict:
    """对照项目生效时间轴与交付字幕，返回逐条结论与五类数量。

    project_lines / delivery_lines 均按各自文件顺序。返回：
      project_items[i] / delivery_items[j]：conclusion 与 candidates
        （等优对应中该行的候选对方下标，0 基，截断至 _MAX_CANDIDATES）；
      summary：pass / time_mismatch / missing / added / pending 数量
        （通过、时间不符、缺失统计项目侧条目，新增统计交付侧条目，
        待确认两侧合计）。
    """
    m, n = len(project_lines), len(delivery_lines)
    if (m + 1) * (n + 1) > _MAX_CELLS:
        raise ValueError(
            f"条目规模过大（项目 {m} 条 × 交付 {n} 条），无法核验")

    # 候选对：仅可见文本完全相同者可配对；按项目行收集 {列j(1基): 偏差}
    by_text: dict[str, list[int]] = {}
    for j, dl in enumerate(delivery_lines):
        by_text.setdefault(dl.text, []).append(j)
    devs_by_row: list[dict[int, int]] = []
    max_dev = 0
    for pl in project_lines:
        cols: dict[int, int] = {}
        for j in by_text.get(pl.text, ()):
            dl = delivery_lines[j]
            dev = (abs(pl.start_ms - dl.start_ms)
                   + abs(pl.end_ms - dl.end_ms))
            cols[j + 1] = dev
            if dev > max_dev:
                max_dev = dev
        devs_by_row.append(cols)

    # 分值编码：score = 对数 * C − 偏差和。C 须大于任何可达偏差和
    # （≤ min(m,n) * max_dev），保证最大化 score 等价于先最多对数、
    # 再最小偏差和；每对权重 w = C − dev ≥ 1，匹配永远有利。
    c_cap = max_dev * min(m, n) + 1
    if min(m, n) * c_cap >= (1 << 62):
        raise ValueError("条目数量或时间范围过大，无法核验")
    wrows: list[dict[int, int]] = [dict()]  # 1 基行：wrows[i] 对应项目行 i-1
    for cols in devs_by_row:
        wrows.append({j: c_cap - dev for j, dev in cols.items()})

    # 前缀表 F（全量保存，供后缀扫描时判定最优对/最优跳过）
    rows_f = [array("q", bytes(8 * (n + 1)))]
    for i in range(1, m + 1):
        rows_f.append(_fwd_row(rows_f[-1], wrows[i]))
    total = rows_f[m][n]

    proj_possible: list[set[str]] = [set() for _ in range(m)]
    del_possible: list[set[str]] = [set() for _ in range(n)]
    proj_cands: list[list[int]] = [[] for _ in range(m)]
    del_cands: list[list[int]] = [[] for _ in range(n)]

    def keep(cands: list[int], idx: int) -> None:
        if len(cands) < _MAX_CANDIDATES:
            cands.append(idx)

    # 后缀扫描：b_next 始终为 B[i+1]。先处理 i = m 的交付列跳过判定。
    b_next = array("q", bytes(8 * (n + 1)))
    f_m = rows_f[m]
    for j in range(n):
        if f_m[j] + b_next[j + 1] == total:
            del_possible[j].add(ADDED)
    for i in range(m - 1, -1, -1):
        w_next = wrows[i + 1]
        b_cur = _bwd_row(b_next, w_next)
        f_i = rows_f[i]
        # 项目行 i 可在某最优对应中未匹配：F[i][j] + B[i+1][j] == S
        for j in range(n + 1):
            if f_i[j] + b_next[j] == total:
                proj_possible[i].add(MISSING)
                break
        # 最优对 (i, j0)：F[i][j0] + w(i,j0) + B[i+1][j0+1] == S；
        # j1 = j0+1 为 1 基列，即 F[i][j1-1] + w + B[i+1][j1] == S
        pl = project_lines[i]
        for j1, w in w_next.items():
            if f_i[j1 - 1] + w + b_next[j1] == total:
                j0 = j1 - 1
                dl = delivery_lines[j0]
                concl = (PASS if (pl.start_ms == dl.start_ms
                                  and pl.end_ms == dl.end_ms)
                         else TIME_MISMATCH)
                proj_possible[i].add(concl)
                del_possible[j0].add(concl)
                keep(proj_cands[i], j0)
                keep(del_cands[j0], i)
        # 交付列 j 可在某最优对应中未匹配：F[i][j] + B[i][j+1] == S
        for j in range(n):
            if f_i[j] + b_cur[j + 1] == total:
                del_possible[j].add(ADDED)
        b_next = b_cur

    project_items = [{
        "index": i,
        "conclusion": _resolve(proj_possible[i]),
        "candidates": sorted(proj_cands[i]),
    } for i in range(m)]
    delivery_items = [{
        "index": j,
        "conclusion": _resolve(del_possible[j]),
        "candidates": sorted(del_cands[j]),
    } for j in range(n)]

    def count(items: list[dict], concl: str) -> int:
        return sum(1 for it in items if it["conclusion"] == concl)

    summary = {
        "project_total": m,
        "delivery_total": n,
        "pass": count(project_items, PASS),
        "time_mismatch": count(project_items, TIME_MISMATCH),
        "missing": count(project_items, MISSING),
        "added": count(delivery_items, ADDED),
        "pending": count(project_items, PENDING) + count(delivery_items, PENDING),
    }
    return {
        "summary": summary,
        "project_items": project_items,
        "delivery_items": delivery_items,
    }
