"""交付字幕核验的回归测试。

覆盖：
- 匹配器单元：完全一致、重编号/插删行、时间不符、顺序保持（交换→待确认）、
  重复文案（多对一/一对多→待确认）、等偏差并列、最多对数优先于偏差、
  拆并行不自动合并、无共同文案、空侧；
- 端到端：生效时间轴优先级（已采纳 → 提案 → 原值）、汇总数量与候选对应、
  VTT、空稿/解析失败/项目不存在报错；
- 只读性：核验不更改采纳、留白结论与 JSON/SRT/ZIP 导出（字节级一致）。
"""
import asyncio
import io
import json
import os
import sys
import zipfile

DB = "/tmp/test_delivery_check.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_PATH"] = DB  # 须在导入 app 之前设置

sys.path.insert(0, "/workspace")

from fastapi import HTTPException, UploadFile  # noqa: E402

from app import database as db  # noqa: E402
from app import main as m  # noqa: E402
from app.delivery import (ADDED, MISSING, PASS, PENDING, TIME_MISMATCH,  # noqa: E402
                          CheckLine, verify_delivery)

db.init_db()


def L(text, s, e):
    return CheckLine(text=text, start_ms=s, end_ms=e)


def _concls(items):
    return [it["conclusion"] for it in items]


def _check(proj, deliv):
    return verify_delivery(proj, deliv)


# ---------- 匹配器单元 ----------

def test_unit_exact_match_all_pass():
    r = _check([L("甲", 0, 1000), L("乙", 1000, 2000)],
               [L("甲", 0, 1000), L("乙", 1000, 2000)])
    assert _concls(r["project_items"]) == [PASS, PASS]
    assert _concls(r["delivery_items"]) == [PASS, PASS]
    assert r["summary"] == {"project_total": 2, "delivery_total": 2,
                            "pass": 2, "time_mismatch": 0, "missing": 0,
                            "added": 0, "pending": 0}
    print("unit exact match ok")


def test_unit_insert_delete_renumber():
    # 交付稿重编号（不影响，按文案+顺序）、删 B、插 X
    r = _check([L("A", 0, 100), L("B", 100, 200), L("C", 200, 300),
                L("D", 300, 400)],
               [L("A", 0, 100), L("X", 150, 180), L("C", 200, 300),
                L("D", 300, 400)])
    assert _concls(r["project_items"]) == [PASS, MISSING, PASS, PASS]
    assert _concls(r["delivery_items"]) == [PASS, ADDED, PASS, PASS]
    s = r["summary"]
    assert (s["pass"], s["missing"], s["added"], s["pending"]) == (3, 1, 1, 0)
    # 候选对应：项目 C ↔ 交付第 3 行（0 基下标 2）
    assert r["project_items"][2]["candidates"] == [2]
    assert r["delivery_items"][2]["candidates"] == [2]
    print("unit insert/delete ok")


def test_unit_time_mismatch():
    r = _check([L("甲", 0, 1000)], [L("甲", 0, 1200)])
    assert _concls(r["project_items"]) == [TIME_MISMATCH]
    assert _concls(r["delivery_items"]) == [TIME_MISMATCH]
    assert r["summary"]["time_mismatch"] == 1
    print("unit time mismatch ok")


def test_unit_order_swap_all_pending():
    # 顺序交换：最多匹配 1 对，A-A 与 B-B 等优 → 全部待确认
    r = _check([L("A", 0, 100), L("B", 100, 200)],
               [L("B", 100, 200), L("A", 0, 100)])
    assert _concls(r["project_items"]) == [PENDING, PENDING]
    assert _concls(r["delivery_items"]) == [PENDING, PENDING]
    assert r["summary"]["pending"] == 4
    print("unit order swap pending ok")


def test_unit_duplicate_delivery_lines():
    # 项目 1 行、交付 2 行完全相同：项目行必然通过；交付行各自可能
    # 被匹配或新增 → 待确认
    r = _check([L("你好", 1000, 2000)],
               [L("你好", 1000, 2000), L("你好", 1000, 2000)])
    assert _concls(r["project_items"]) == [PASS]
    assert _concls(r["delivery_items"]) == [PENDING, PENDING]
    s = r["summary"]
    assert (s["pass"], s["added"], s["pending"]) == (1, 0, 2)
    print("unit duplicate delivery ok")


def test_unit_duplicate_project_lines():
    # 项目 2 行相同、交付 1 行：交付行必然通过；项目行各自可能
    # 通过或缺失 → 待确认
    r = _check([L("你好", 1000, 2000), L("你好", 1000, 2000)],
               [L("你好", 1000, 2000)])
    assert _concls(r["project_items"]) == [PENDING, PENDING]
    assert _concls(r["delivery_items"]) == [PASS]
    s = r["summary"]
    assert (s["pass"], s["missing"], s["pending"]) == (0, 0, 2)
    print("unit duplicate project ok")


def test_unit_equal_deviation_tie():
    # 两个交付候选与项目行偏差相同（各 100ms）：项目行在任一最优对应中
    # 都是时间不符（结论唯一，不是待确认）；交付行各自可能不符或新增
    r = _check([L("x", 1000, 2000)],
               [L("x", 900, 2000), L("x", 1100, 2000)])
    assert _concls(r["project_items"]) == [TIME_MISMATCH]
    assert _concls(r["delivery_items"]) == [PENDING, PENDING]
    s = r["summary"]
    assert (s["time_mismatch"], s["pending"]) == (1, 2)
    # 偏差更小的唯一最优：精确匹配优先
    r2 = _check([L("x", 1000, 2000)],
                [L("x", 1000, 2000), L("x", 1100, 2000)])
    assert _concls(r2["project_items"]) == [PASS]
    assert _concls(r2["delivery_items"]) == [PASS, ADDED]
    print("unit equal deviation tie ok")


def test_unit_max_pairs_beats_zero_deviation():
    # 对数优先：两对（含一对有偏差）优于仅一对零偏差
    r = _check([L("A", 0, 100), L("B", 100, 200)],
               [L("A", 0, 100), L("B", 105, 205)])
    assert _concls(r["project_items"]) == [PASS, TIME_MISMATCH]
    assert _concls(r["delivery_items"]) == [PASS, TIME_MISMATCH]
    print("unit max pairs priority ok")


def test_unit_local_ambiguity_does_not_spread():
    # 重复只影响局部：A 行重复 → 交付两个 A 待确认，其余行结论确定
    r = _check([L("A", 0, 100), L("B", 100, 200)],
               [L("A", 0, 100), L("A", 0, 100), L("B", 100, 200)])
    assert _concls(r["project_items"]) == [PASS, PASS]
    assert _concls(r["delivery_items"]) == [PENDING, PENDING, PASS]
    assert r["summary"]["pending"] == 2
    print("unit local ambiguity ok")


def test_unit_split_not_merged():
    # 拆并行不自动合并：交付把一行拆成两行 → 项目缺失 + 两条新增
    r = _check([L("长文本句子", 0, 2000)],
               [L("长文本", 0, 1000), L("句子", 1000, 2000)])
    assert _concls(r["project_items"]) == [MISSING]
    assert _concls(r["delivery_items"]) == [ADDED, ADDED]
    s = r["summary"]
    assert (s["missing"], s["added"]) == (1, 2)
    print("unit split not merged ok")


def test_unit_no_common_text_and_empty_side():
    r = _check([L("甲", 0, 100), L("乙", 100, 200)],
               [L("丙", 0, 100), L("丁", 100, 200)])
    assert _concls(r["project_items"]) == [MISSING, MISSING]
    assert _concls(r["delivery_items"]) == [ADDED, ADDED]
    assert r["summary"]["pending"] == 0
    # 一侧为空：另一侧全部未匹配（端点会拒绝空交付稿，匹配器仍可计算）
    r2 = _check([], [L("丙", 0, 100)])
    assert _concls(r2["delivery_items"]) == [ADDED]
    r3 = _check([L("甲", 0, 100)], [])
    assert _concls(r3["project_items"]) == [MISSING]
    print("unit no common text / empty side ok")


def test_unit_scale_smoke():
    # 300 行含重复文案与插删：应在常规耗时内完成且结论确定
    proj, deliv = [], []
    for i in range(300):
        proj.append(L(f"台词{i % 50}", i * 1000, i * 1000 + 800))
    for i in range(300):
        if i == 100:
            deliv.append(L("插入行", i * 1000 + 100, i * 1000 + 200))
        if i == 200:
            continue  # 删除一行
        deliv.append(L(f"台词{i % 50}", i * 1000, i * 1000 + 800))
    r = _check(proj, deliv)
    s = r["summary"]
    assert s["pass"] == 299 and s["missing"] == 1 and s["added"] == 1, s
    print("unit scale smoke ok")


def test_unit_scale_guard():
    # 规模上限：超出 DP 网格预算 → ValueError（端点转 400）
    import app.delivery as dlv
    saved = dlv._MAX_CELLS
    dlv._MAX_CELLS = 100
    try:
        try:
            _check([L("甲", 0, 100)] * 20, [L("甲", 0, 100)] * 20)
            raise AssertionError("超规模应报错")
        except ValueError as exc:
            assert "规模过大" in str(exc)
    finally:
        dlv._MAX_CELLS = saved
    print("unit scale guard ok")


# ---------- 端到端 ----------

def _upload(name: str, content: str) -> UploadFile:
    return UploadFile(io.BytesIO(content.encode("utf-8")), filename=name)


SRT2 = ("1\n00:00:00,000 --> 00:00:01,000\n甲\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n乙\n")
SHOTS = json.dumps([{"start_ms": 0, "end_ms": 3000}])


def _create(srt=SRT2, gap=40, tol=200, shots=None):
    return asyncio.run(m.create_project(
        name="交付核验场景", cps_limit=21.0, min_gap_ms=gap, tolerance_ms=tol,
        subtitle=_upload("a.srt", srt),
        shots_file=_upload("s.json", shots) if shots else None,
    ))


def _check_api(pid, content, name="delivery.srt"):
    return asyncio.run(m.delivery_check(
        pid, delivery_file=_upload(name, content)))


def _expect_http(status, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        raise AssertionError(f"应抛出 {status}")
    except HTTPException as exc:
        assert exc.status_code == status, exc.status_code
        return exc


def test_e2e_effective_timeline_priority():
    # 含镜头（容差经镜头窗口生效）。提案：c1 0–1000（不动），c2 1040–2040；
    # 生效时间轴未采纳即取提案
    proj = _create(shots=SHOTS)
    pid = proj["id"]
    cues = proj["analysis"]["cues"]
    assert [(c["proposal_start_ms"], c["proposal_end_ms"]) for c in cues] == [
        (0, 1000), (1040, 2040)]

    # 交付稿用原值：c2 与生效时间轴（提案 1040–2040）不符
    r = _check_api(pid, SRT2)
    assert _concls(r["project_items"]) == [PASS, TIME_MISMATCH]
    assert r["summary"]["time_mismatch"] == 1

    # 交付稿用提案值：全部通过
    r = _check_api(pid, "7\n00:00:00,000 --> 00:00:01,000\n甲\n\n"
                        "9\n00:00:01,040 --> 00:00:02,040\n乙\n")
    assert _concls(r["project_items"]) == [PASS, PASS]
    assert r["summary"]["pass"] == 2

    # 采纳 c2@1040–2040 后收紧 min_gap 到 30：提案变为 1030–2030，
    # 采纳仍合法 → 生效时间轴取采纳值 1040–2040
    proj = m.adopt(pid, m.AdoptIn(cue_uid=cues[1]["uid"],
                                  new_start_ms=1040, new_end_ms=2040))
    proj = m.update_settings(pid, m.SettingsIn(
        cps_limit=21.0, min_gap_ms=30, tolerance_ms=200))
    assert proj["analysis"]["cues"][1]["adopted"] is True
    assert proj["analysis"]["cues"][1]["proposal_start_ms"] == 1030
    r = _check_api(pid, "1\n00:00:00,000 --> 00:00:01,000\n甲\n\n"
                        "2\n00:00:01,040 --> 00:00:02,040\n乙\n")
    assert _concls(r["project_items"]) == [PASS, PASS]          # 采纳优先
    r = _check_api(pid, "1\n00:00:00,000 --> 00:00:01,000\n甲\n\n"
                        "2\n00:00:01,030 --> 00:00:02,030\n乙\n")
    assert _concls(r["project_items"]) == [PASS, TIME_MISMATCH]  # 提案不算

    # 收紧容差到 30（最小间隔回到 40）：采纳被撤销；c1 进冲突链无提案
    # → 生效时间轴回退原值；c2 提案恰为原值，生效时间轴同为原值
    proj = m.update_settings(pid, m.SettingsIn(
        cps_limit=21.0, min_gap_ms=40, tolerance_ms=30))
    assert proj["analysis"]["cues"][0]["proposal_start_ms"] is None
    assert proj["analysis"]["cues"][1]["adopted"] is False
    assert proj["analysis"]["cues"][1]["proposal_start_ms"] == 1000
    r = _check_api(pid, SRT2)
    assert _concls(r["project_items"]) == [PASS, PASS]          # 回退原值
    print("e2e effective timeline priority ok")


def test_e2e_summary_candidates_and_renumber():
    proj = _create(gap=0)  # 提案=原值，生效时间轴即原时间
    pid = proj["id"]
    # 重编号 + 插入 + 时间不符
    r = _check_api(pid,
                   "10\n00:00:00,000 --> 00:00:01,000\n甲\n\n"
                   "20\n00:00:01,500 --> 00:00:01,700\n插入行\n\n"
                   "30\n00:00:01,000 --> 00:00:02,100\n乙\n")
    s = r["summary"]
    assert (s["pass"], s["time_mismatch"], s["missing"],
            s["added"], s["pending"]) == (1, 1, 0, 1, 0), s
    assert s["project_total"] == 2 and s["delivery_total"] == 3
    # 项目侧候选对应引用交付 ord；交付侧引用项目 ord/cue_id
    assert r["project_items"][0]["candidates"] == [
        {"ord": 1, "start_ms": 0, "end_ms": 1000}]
    assert r["project_items"][1]["candidates"] == [
        {"ord": 3, "start_ms": 1000, "end_ms": 2100}]
    assert r["delivery_items"][1]["candidates"] == []  # 插入行无对应
    assert r["delivery_items"][2]["candidates"][0]["cue_id"] == "2"
    assert r["file_name"] == "delivery.srt"
    print("e2e summary + candidates ok")


def test_e2e_vtt_and_pending_view():
    proj = _create(srt="1\n00:00:00,000 --> 00:00:01,000\n你好\n", gap=0)
    pid = proj["id"]
    vtt = ("WEBVTT\n\n"
           "00:00:00.000 --> 00:00:01.000\n你好\n\n"
           "00:00:00.000 --> 00:00:01.000\n你好\n")
    r = _check_api(pid, vtt, name="delivery.vtt")
    assert _concls(r["project_items"]) == [PASS]
    assert _concls(r["delivery_items"]) == [PENDING, PENDING]
    assert r["summary"]["pending"] == 2
    print("e2e vtt + pending ok")


def test_e2e_errors():
    proj = _create()
    pid = proj["id"]
    # 空稿 → 400
    exc = _expect_http(400, _check_api, pid, "")
    assert "未解析出任何条目" in exc.detail
    # 只有时间戳没有可解析块 → 400
    exc = _expect_http(400, _check_api, pid, "随便一段不是字幕的文本\n")
    assert "未解析出任何条目" in exc.detail
    # 非法字节 → 400 解析失败
    try:
        asyncio.run(m.delivery_check(
            pid, delivery_file=UploadFile(io.BytesIO(b"\xff\xfe\x00bad"),
                                          filename="bad.srt")))
        raise AssertionError("非法字节应报错")
    except HTTPException as exc2:
        assert exc2.status_code == 400 and "解析失败" in exc2.detail
    # 项目不存在 → 404
    _expect_http(404, _check_api, 99999, SRT2)
    print("e2e errors ok")


def test_e2e_read_only():
    # 含镜头项目：留白标记 + 采纳后做核验，核验前后一切状态不变
    proj = _create(shots=SHOTS)
    pid = proj["id"]
    cues = proj["analysis"]["cues"]
    proj = m.adopt(pid, m.AdoptIn(cue_uid=cues[1]["uid"],
                                  new_start_ms=1040, new_end_ms=2040))
    proj = m.review_gap(pid, m.GapReviewIn(
        shot_ord=1, gap_start_ms=2040, gap_end_ms=3000,
        decision="intentional"))
    detail_before = m.get_project(pid)
    json_before = m.export(pid, fmt="json").body
    srt_before = m.export(pid, fmt="srt").body
    with zipfile.ZipFile(io.BytesIO(m.export(pid, fmt="zip").body)) as zf:
        zip_before = {n: zf.read(n) for n in zf.namelist()}

    # 核验（含缺失/新增/不符的非平凡结果）
    r = _check_api(pid, "1\n00:00:00,500 --> 00:00:01,000\n甲\n\n"
                        "2\n00:00:01,040 --> 00:00:02,040\n新文案\n")
    assert r["summary"]["missing"] == 1 and r["summary"]["added"] == 1

    detail_after = m.get_project(pid)
    assert detail_after == detail_before            # 采纳/留白/分析均不变
    marked = [it for it in detail_after["gap_review"]["items"]
              if it["gap_start_ms"] == 2040]
    assert marked and marked[0]["decision"] == "intentional"
    assert m.export(pid, fmt="json").body == json_before
    assert m.export(pid, fmt="srt").body == srt_before
    with zipfile.ZipFile(io.BytesIO(m.export(pid, fmt="zip").body)) as zf:
        assert {n: zf.read(n) for n in zf.namelist()} == zip_before
    # 数据库中无核验残留：表结构与行数不变
    with db.get_conn() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        assert tables == ["adoptions", "cues", "gap_reviews",
                          "projects", "shots", "sqlite_sequence"], tables
    print("e2e read only ok")


def main():
    test_unit_exact_match_all_pass()
    test_unit_insert_delete_renumber()
    test_unit_time_mismatch()
    test_unit_order_swap_all_pending()
    test_unit_duplicate_delivery_lines()
    test_unit_duplicate_project_lines()
    test_unit_equal_deviation_tie()
    test_unit_max_pairs_beats_zero_deviation()
    test_unit_local_ambiguity_does_not_spread()
    test_unit_split_not_merged()
    test_unit_no_common_text_and_empty_side()
    test_unit_scale_smoke()
    test_unit_scale_guard()
    test_e2e_effective_timeline_priority()
    test_e2e_summary_candidates_and_renumber()
    test_e2e_vtt_and_pending_view()
    test_e2e_errors()
    test_e2e_read_only()
    print("delivery check regression ok")


if __name__ == "__main__":
    main()
