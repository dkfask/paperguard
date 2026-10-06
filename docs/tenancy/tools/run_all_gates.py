#!/usr/bin/env python3
"""门禁套件统一入口

为什么需要它
------------
`docs/tenancy/tools/` 下已经有 19 个门禁工具（其中 18 个纳入本套件，
`run_all_gates.py` 自己除外），它们各有退出码语义，
但**没有任何东西会主动去跑它们**——门禁靠人记得敲，就等于没有门禁。
本脚本把它们收成一条命令，并给出两种模式供不同场合使用。

三态退出码（每个工具统一遵守）
------------------------------
    0 = 检查通过
    1 = **检查未通过**：差距是真实的，已被记录并跟踪（例如契约债、数据缺口）
    2 = **工具不可运行**：缺依赖、缺输入、路径不存在——门禁本身坏了

两种模式
--------
    --mode ci       只在出现 2 时失败。理由：1 表示「差距已量化并登记」，
                    这是预期状态，拿它拦 CI 会让流水线从第一天起就是红的，
                    反而没人再看。2 表示「门禁自己坏了」，必须拦。
    --mode strict   出现 1 或 2 都失败。用于发版前人工把关。

    `--mode report` 等同 `--mode ci` 但退出码恒为 0（只出报告）。

输入漂移
--------
门禁跑在 `docs/product/*.md`、`docs/tenancy/*.md` 这些「设计真相」文件上。
若它们在运行期间被改写，本次结果就是半旧半新的混合物，报出来的红/绿
**都不能当证据**。运行前后各取一次 sha256 快照并逐条比对；有漂移时
`ci`/`strict` 返回 2（不是 1——`1` 的语义是「差距真实且已登记」，
而漂移是「本次结果压根不是证据」），`report` 仍返回 0 但把漂移文件
写进 JSON 的 `input_drift` 字段供取证。见 INC-68。

刻意不做的事
------------
1. **不把多个门禁聚合成一个数字**。总数会掩盖「哪一道坏了」，
   也掩盖了「一道都没跑起来」和「都跑绿了」的区别。
2. **不把 2 当成 1**。工具坏了就是工具坏了，不该混进「未通过」里——
   否则你会以为门禁正常工作、只是差距还在。
3. **不吞掉输出**。每道门禁的 stderr/stdout 摘要都会带出来。

退出码
------
    0 = 达到本模式的通过条件
    1 = 未达到（见各模式定义）
    2 = 本工具自身无法运行
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

# 下游工具读它以复用已跑结果（INC-122）。**必须与 design_doc_check.RESULT_VAR 同名**——
# 两处各写一个字符串常量，就是下一个「副本各自腐烂」的入口（INC-78）。
RESULT_VAR = "PAPERGUARD_GATE_RESULTS"

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

# 门禁清单。kind 决定怎么跑：
#   self-test  跑 --self-test（阳性对照：证明工具能抓错）
#   plain      直接跑（检查当前代码状态）
#   skip-if-missing  缺输入时应当报 2（工具不可运行），不是 1
GATES = [
    ("backfill_validate.py", "self-test", "E5 回填校验器自检（7/7 注入捕获）"),
    ("ownership_preflight.py", "self-test", "只读归属准入行为负例/篡改变异/无副作用合成演练"),
    ("backfill_migrate.py", "self-test", "E5 回填器自检（变异 + 幂等 + 拒绝猜测）"),
    ("tenant_map_candidates.py", "self-test", "Q-01 映射候选/校验自检"),
    ("account_path_check.py", "self-test", "建号路径契约自检（AST 静态分析）"),
    ("ddl_static_check.py", "self-test", "DDL 判定逻辑自检（不依赖 pglast）"),
    ("ddl_static_check.py", "plain", "DDL 语法 + 外键目标可解析性"),
    ("rls_policy_check.py", "self-test", "RLS 策略矩阵判定自检（覆盖面闸 + 变异）"),
    ("rls_policy_check.py", "plain", "RLS 策略矩阵核对（13 张新表）"),
    ("contract_readiness_check.py", "self-test", "contract 准入判定自检（ANSI 解析 + 变异）"),
    ("contract_readiness_check.py", "plain", "contract 阶段准入条件核对（实跑隔离清单）"),
    ("context_resolution_check.py", "self-test", "W-03 上下文解析判定自检（类型消歧 + 变异）"),
    ("context_resolution_check.py", "plain", "W-03 RequestContext 不变量与失败出口（CR-1～CR-4）"),
    ("spec_citation_check.py", "self-test", "规格引用核对自检（窗口边界 + 噪音排除）"),
    ("spec_citation_check.py", "plain", "W-01～W-05 规格代码引用行号与关键字核对"),
    ("schema_disposition_check.py", "self-test", "E4 逐表判定解析自检"),
    ("schema_disposition_check.py", "plain", "E4 逐表判定三方交叉核对"),
    ("design_doc_check.py", "self-test", "设计文档自洽门禁自检"),
    ("design_doc_check.py", "plain", "设计文档与门禁现实一致（DC-0～DC-9、DC-11、DC-12；DC-10 已预留未实施）"),
    ("api_contract_check.py", "plain", "OpenAPI 契约 9 项"),
    # 此前只登记了 plain。**该工具实现了 9/9 阳性对照却从未被套件执行**，
    # 与本文件自己的 --self-test 曾被漏登记是同一种缺口（INC-70）。
    ("api_contract_check.py", "self-test", "OpenAPI 契约检查逻辑的阳性对照（9 项）"),
    ("at_traceability_check.py", "self-test", "AT 追溯阳性对照（AT-10 归属边界）"),
    ("at_traceability_check.py", "plain", "AT 矩阵与设计文档逐字一致（AT-01～AT-10）"),
    ("resource_registry_check.py", "self-test", "资源登记表访问面判定自检（枚举/实算/幽灵/口径）"),
    # INC-85：`resource-registry.yaml` 是**租户隔离改造的唯一入口清单**，
    # 而它的「访问面全集」此前只以一个数字（count: 111）存在、零个方法名。
    ("resource_registry_check.py", "plain", "资源登记表 ↔ 真实代码（RR-1 枚举/覆盖、RR-2 实算、RR-3 幽灵、RR-4 口径）"),
    # INC-86 残留风险：`_metered_chat` 是**唯一计量出口**，而此前**没有任何机制**
    # 阻止新增 `_chat_completion` 直调绕过它。本工具就是那道静态门禁。
    ("ai_egress_check.py", "self-test", "AI 出站计量出口守卫自检（AE-0～AE-5）"),
    ("ai_egress_check.py", "plain", "AI 出站收敛：唯一计量出口 / 配额门禁 / 异常路径记账 / 跨模块不得绕过 / 运行时用例路径级差集"),
    ("error_codes_check.py", "self-test", "错误码 EC-06 占位/已实现重名自检"),
    ("error_codes_check.py", "plain", "错误码登记一致性"),
    ("lease_guard_check.py", "self-test", "空租约通配检出自检（阳性/阴性/变异）"),
    ("lease_guard_check.py", "plain", "INC-47 租约栅栏：空租约通配检出"),
    ("spec_rule_id_check.py", "self-test", "规格规则 ID 唯一性自检（定义/引用消歧）"),
    ("spec_rule_id_check.py", "plain", "INC-49 跨规格规则 ID 重复定义"),
    ("spec_xref_check.py", "self-test", "规格章节交叉引用自检（断链 + 跨规格不误报 + 防空转）"),
    ("spec_xref_check.py", "plain", "INC-98 规格 §N 交叉引用指向真实章节"),
    ("secret_scan_check.py", "self-test", "敏感信息扫描自检（阳性/阴性/模板边界）"),
    ("secret_scan_check.py", "plain", "§22.4 敏感信息与冲突标记扫描"),
    # §22.4 第 6 条的另一半（另一半是上面的 secret_scan_check）。
    # ★ 本条**在 CI 上必须能核对**：工作区是干净 checkout，所以工具自己读
    #   GITHUB_BASE_REF 取 origin/<base>..HEAD；base 解析不到时它**报红**，
    #   而不是退回「扫工作区」然后恒绿。
    ("whitespace_check.py", "self-test", "行尾空白判定自检（md 硬换行只放行恰好 2 个空格）"),
    ("whitespace_check.py", "plain", "§22.4 第 6 条：git diff --check + 未跟踪文件全文"),
    ("synthetic_data.py", "plain", "验收前置数据缺口探测"),
    # 本工具曾**既无 --self-test 也无变异记录**，而 §27.3 却写着「每道门禁都有
    # --self-test……一个抓不住错误的检查器不是检查器」。INC-70 补上 19 项自检。
    ("synthetic_data.py", "self-test", "前置数据探测的阳性对照与静默失效防护"),
    ("account_path_check.py", "plain", "建号路径契约（真实仓库）"),
    # 门禁入口自己的自检。**此前它不在套件里，于是这 21 项判据没有任何机器会去跑**，
    # 只有我手工敲过一次——「靠人记得敲就等于没有门禁」，这条规则对自己同样成立。
    # 无递归风险：self_test() 里 GATES 被替换成桩，main() 不会再去跑真实门禁；
    # 且它用自己的 `--truth-root` 指向临时目录，不会把真实文档的改动误判成漂移。
    ("run_all_gates.py", "self-test", "门禁入口自检（身份唯一性 + 输入漂移 + 三态不塌缩）"),
]

VERDICT = {
    0: ("PASS", GREEN, "通过"),
    1: ("GAP", YELLOW, "未通过（差距已记录）"),
    2: ("BROKEN", RED, "工具不可运行"),
}

# ---------------------------------------------------------------------------
# 「本模式不适用」的门禁（INC-94）
# ---------------------------------------------------------------------------
# 判据：**一道门禁的主语不在版本控制里，它就不可能是 CI 门禁。**
#
# `contract_readiness_check` 核对的是**本地开发库里的隔离清单**，
# 而 `backend/data/` 在 `.gitignore:75` 被刻意排除，CI 的干净检出里没有它。
# 实测（干净克隆，`git clone` 到 /tmp 后实跑）：
#   · 缺库 ⇒ `找不到数据库` ⇒ **exit=2**
#   · 补一个「只含表、无数据」的库 ⇒ 仍是 exit=2，
#     原因是「隔离清单段落存在但 0 条——解析失败」
#     （隔离清单来自对**有数据**的源库做 dry-run，空库给不出清单）
# 而 `--mode ci` 只拦退出码 2 ⇒ **流水线第一次跑就会永久红**。
#
# 为什么不能用「让它 exit=0」解决：那是**空洞通过**——
# 它没核对任何东西，却报「通过」。本项目反复吃过这个亏
# （INC-56 假门禁、INC-70 恒绿、INC-92 空规格判绿）。
# 也不能用「exit=1（差距已登记）」：它确实报出了 CT-2/CT-3 两条**真实**的
# contract 缺口（INC-03/INC-04 未裁决），但那些缺口与 CI 无关，
# 记成「差距」会让本地那 6 道常红门禁的名单在 CI 里变得不可解释。
#
# 所以引入**第四种状态**：跑、如实报出结果、**不计入三态、不参与退出码**，
# 且在输出与 `--json` 里**单独列出并附理由**——绝不静默丢弃。
# 只有 `--mode ci` 生效；`strict` / `report` 下它们照常参与判定。
CI_INAPPLICABLE: dict[str, str] = {
    "contract_readiness_check.py::plain": (
        "主语是本地开发库的隔离清单，backend/data/ 不在版本控制里；"
        "空库下 dry-run 给不出清单（实测 exit=2）"),
}



def gate_id(r: dict) -> str:
    """门禁身份 = 工具名 + kind。

    **不能只用工具名做键（INC-67）**：同一个工具常有 self-test 与 plain 两次调用，
    两者退出码可以不同（实测 13 个工具各被记两次，其中 5 个两次的码不同）。
    只按工具名做键时后者静默覆盖前者，`--json` 的 `exit` 会从 31 项塌缩成 18 项——
    而 `total` 仍然是 31，**看上去完整，实际上丢了 13 次观测**。
    今天的「幸存值恰好是更严的那个」只是 GATES 清单里 self-test 排在 plain 前面的巧合，
    没有任何规则保证它；重排清单就会静默翻转。
    """
    return f"{r['name']}::{r['kind']}"


# 门禁的「设计真相」输入。运行期间这些文件若发生变化，本次结果是在**漂移的输入**上
# 算出来的，不能当证据用。
TRUTH_GLOBS = ("product/*.md", "product/W-*.md", "tenancy/*.md")


def snapshot_inputs(root: pathlib.Path) -> dict[str, str]:
    import hashlib
    seen: dict[str, str] = {}
    for pat in TRUTH_GLOBS:
        for p in sorted(root.glob(pat)):
            if p.is_file():
                seen[str(p.relative_to(root))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
    return seen


def drifted(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changed = {k for k in set(before) | set(after)
               if before.get(k) != after.get(k)}
    return sorted(changed)


def is_crash(out: str) -> bool:
    """输出里有没有 Python 未捕获异常的痕迹。

    判据只认**末行异常摘要**那一行格式（`Traceback (most recent call last):`
    或以 `XxxError: ...` 结尾），**不看正文里是否出现过 "Traceback" 字样**——
    工具完全可能在**正常报告里**提到这个词（自检夹具的样本文本就会）。
    **只按子串判断会把「如实说明」判成「崩溃」，那会让门禁第一次就报假警。**
    """
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return False
    if "Traceback (most recent call last)" in out and any(
            "Traceback (most recent call last)" in ln for ln in lines[-12:]):
        return True
    return bool(re.search(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception)\b",
                          lines[-1]))


def result_key(name: str, kind: str) -> str:
    """门禁结果的**唯一编码**。写端与读端必须共用它（INC-117/INC-122）。

    `kind == "plain"` 必须编码成**空 flags**，而 `" ".join([])` 恰好是 `""`，
    于是 `design_doc_check.py::` 与 `design_doc_check.py --self-test::--self-test`
    在字符串层面就能区分——`partition("::")` 切回来时不会把两者混成一个键。
    """
    flags = "--self-test" if kind == "self-test" else ""
    return f"{name}::{flags}"


def run_gate(tool: pathlib.Path, kind: str, env_extra: dict | None = None) -> dict:
    cmd = [sys.executable, str(tool)] + (["--self-test"] if kind == "self-test" else [])
    t0 = time.monotonic()
    # ★ INC-122：把**已经跑过**的门禁结果交给下游工具，让它不必重复执行。
    # 起因是实测：`design_doc_check.py --plain` 要实跑自己的 `--self-test`
    # 来核对退出码，而套件上一道门禁刚跑过它 ⇒ 本机 192.9s、CI 300.1s
    # 撞穿单门禁阈值 ⇒ 记「工具不可运行」=2 ⇒ ci 模式报红。
    # 挡掉那次重复 spawn 后本机只要 **7.9s**。
    #
    # ⚠ 这里传的是**刚刚跑出来的真实结果**，不是任何缓存；
    # 下游拿到的退出码与它自己跑出来的一致，核对强度不变。
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        code = p.returncode
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = EXIT_TOOL_BROKEN, "超时（>300s），按工具不可运行处理"
    except OSError as e:
        code, out = EXIT_TOOL_BROKEN, f"无法启动：{e}"
    if code not in VERDICT:
        # 工具返回了约定外的码——不能当成通过，记为工具坏了
        out = f"退出码 {code} 不在约定范围 {{0,1,2}}；\n" + out
        code = EXIT_TOOL_BROKEN
    elif code == EXIT_FINDINGS and is_crash(out):
        # **未捕获异常必须记为「工具坏了」，而不是「有差距」**（2026-10-02 实测）
        # ------------------------------------------------------------------
        # Python 遇到未捕获异常以**退出码 1** 结束，而 1 的约定是
        # 「差距真实且已登记，ci 模式**不拦**」。于是**工具崩溃被伪装成有差距**。
        #
        # 实测事故：SC-10 里一句 `set(...) | known_tables`
        # （`dbpy_tables` 返回的是 dict）抛 `TypeError`，
        # `ddl_static_check` 的退出码从 **2 降成 1**，
        # 于是「本机缺 pglast、工具不可运行」这件事被 CI 静默放过——
        # **回归不是变红了，而是变成了看不见。**
        #
        # 18 个工具里当时只有 1 个有自己的顶层兜底。
        # **在套件层兜住，才不必逐个工具改**；
        # 工具自己仍然应该兜（那样报错信息更准），但**不能只靠工具自觉**。
        out = ("工具以未捕获异常退出（不是「有差距」）。"
               "按三态约定记为工具不可运行。\n") + out
        code = EXIT_TOOL_BROKEN
    return {"code": code, "secs": round(time.monotonic() - t0, 1), "out": out}


def tail(text: str, n: int = 3) -> str:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return " ⏎ ".join(lines[-n:]) if lines else "(无输出)"


# ---------------------------------------------------------------------------
# 自检：用桩门禁证明本入口不会掩盖「工具坏了」
# ---------------------------------------------------------------------------

def _stub(path: pathlib.Path, code: int, extra: str = "") -> None:
    """写一个桩门禁。`extra` 用于制造**带 traceback 的崩溃**（2026-10-02）。

    ⚠ `extra` 是**替换**整个脚本体，不是追加——
    #   第一版写成「在 `def main():` 里再插一段 `import sys`」，
    #   结果**所有 5 个桩全被破坏**（`g_ok` 也崩了），
    #   表现为 3 条既有夹具突然失败。
    #   **改共用夹具生成器时，先看它影响了几条既有夹具。**
    """
    body = extra if extra else f"import sys\nsys.exit({code})\n"
    path.write_text(body, encoding="utf-8")



def self_test() -> int:
    print(f"{DIM}门禁入口自检：证明三态不会被压成一个数字{RESET}\n")
    print("=" * 78)
    import shutil
    import tempfile
    # 固定 /tmp 路径会被嵌套子进程删掉（INC-58），且并发自检会互相踩。
    # tempfile.mkdtemp() 每次独占，且不落在 /tmp 的公共命名空间里。
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gate_runner_selftest_"))
    base = tmp  # 记录初始目录，用于最后核对「自检没动过别的东西」

    # 桩门禁：正常 / 差距未过 / 工具坏了 / 退出码越界
    _stub(tmp / "g_ok.py", 0)
    _stub(tmp / "g_gap.py", 1)
    _stub(tmp / "g_broken.py", 2)
    _stub(tmp / "g_weird.py", 42, extra="import sys\nsys.exit(42)\n")
    # **崩溃桩**：以 1 退出但打印 Python traceback。
    # ⚠ 它与 `g_gap.py` 的**退出码完全一样**——差别只在输出里。
    #   这正是 INC-80 事故的本质：**未捕获异常在退出码上与「有差距」不可区分**，
    #   只能靠输出判别。**少了这个桩，「崩溃被当成差距」就永远测不出来。**
    _stub(tmp / "g_crash.py", 1, extra=(
        '    raise TypeError("unsupported operand type(s) for |: '
        "'set' and 'dict'\")\n"))

    real_gates = GATES[:]
    results: list[tuple[str, bool]] = []

    def t(label: str, cond: bool) -> None:
        results.append((label, cond))
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    def run(dirpath: pathlib.Path, mode: str, gates=None) -> tuple[int, dict | None]:
        """复刻 main() 的判定逻辑，参数化 GATES。

        data 为 None 表示 main() 在产出 JSON 之前就以 2 退出
        （例如清单里出现重复门禁身份）——那正是要测的分支。
        """
        global GATES
        saved = GATES
        GATES = gates if gates is not None else [
            ("g_ok.py", "plain", "ok"), ("g_gap.py", "plain", "gap"),
            ("g_broken.py", "plain", "broken"), ("g_weird.py", "plain", "weird"),
            ("g_crash.py", "plain", "crash")]
        try:
            argv = sys.argv
            sys.argv = ["run_all_gates.py", "--tools-dir", str(dirpath),
                        "--truth-root", str(dirpath),
                        "--mode", mode, "--json"]
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main()
            out = buf.getvalue().strip()
            data = json.loads(out) if out else None
        finally:
            GATES = saved
            sys.argv = argv
        return rc, data

    rc_ci, d_ci = run(tmp, "ci")
    rc_strict, d_strict = run(tmp, "strict")
    rc_report, _ = run(tmp, "report")

    t("通过数正确", d_ci["passed"] == ["g_ok.py::plain"])
    t("差距门禁单列，未与「工具坏了」混淆", d_ci["gaps"] == ["g_gap.py::plain"])
    t("退出码 2 归入 broken", "g_broken.py::plain" in d_ci["broken"])
    t("越界退出码 42 被记为工具坏了（不是通过）", "g_weird.py::plain" in d_ci["broken"])
    # —— INC-80 附带事故：崩溃的退出码**与「有差距」完全一样**（都是 1），
    #    只能靠输出里的 traceback 判别。少了这条，「工具崩溃被 CI 放过」测不出来。
    t("崩溃（退出码 1 + traceback）被记为工具坏了，不是「有差距」",
      "g_crash.py::plain" in d_ci["broken"]
      and "g_crash.py::plain" not in d_ci["gaps"])
    t("五个桩分桶之和 == 总数",
      len(d_ci["passed"]) + len(d_ci["gaps"]) + len(d_ci["broken"]) == d_ci["total"])
    t("ci 模式：有工具坏了 → 失败", rc_ci == EXIT_TOOL_BROKEN)
    t("strict 模式：有任何未通过 → 失败", rc_strict == EXIT_FINDINGS)
    t("report 模式：恒为 0（只出报告）", rc_report == EXIT_OK)
    t("JSON 里保留了逐门禁明细，未被聚合掉",
      len(d_ci["details"]) == 5 and all("tail" in x for x in d_ci["details"]))

    # 只有「差距」没有「工具坏了」时，ci 模式必须放行——否则 CI 从第一天就红
    shutil.rmtree(tmp)
    tmp.mkdir()
    _stub(tmp / "g_ok.py", 0)
    _stub(tmp / "g_gap.py", 1)
    two_only = [("g_ok.py", "plain", "ok"), ("g_gap.py", "plain", "gap")]
    rc_gap_only, d_gap_only = run(tmp, "ci", gates=two_only)
    t("ci 模式：只有差距、无工具坏了 → 放行",
      rc_gap_only == EXIT_OK and d_gap_only["broken"] == [] and
      d_gap_only["gaps"] == ["g_gap.py::plain"])

    # ---- INC-94：「本模式不适用」的门禁 ----
    #
    # 这组夹具的核心是**三态不能塌缩成两态**：
    # 一个 exit=2 的门禁在 ci 模式下被判 N/A 后，
    # 它**必须从 broken 里消失**（否则 CI 永久红），
    # 也**绝不能出现在 passed 里**（那是空洞通过）。
    shutil.rmtree(tmp)
    tmp.mkdir()
    _stub(tmp / "g_ok.py", 0)
    _stub(tmp / "g_broken.py", 2)
    na_only = [("g_ok.py", "plain", "ok"), ("g_broken.py", "plain", "needs-db")]
    saved_na = dict(CI_INAPPLICABLE)
    try:
        CI_INAPPLICABLE.clear()
        CI_INAPPLICABLE["g_broken.py::plain"] = "主语不在版本控制里"
        rc_na, d_na = run(tmp, "ci", gates=na_only)
        rc_na_strict, d_na_strict = run(tmp, "strict", gates=na_only)
    finally:
        CI_INAPPLICABLE.clear()
        CI_INAPPLICABLE.update(saved_na)
    t("N/A 门禁在 ci 模式下不计入 broken（否则 CI 永久红）",
      rc_na == EXIT_OK and d_na["broken"] == [])
    t("★ N/A 门禁绝不在 passed 里（那是空洞通过）",
      d_na["passed"] == ["g_ok.py::plain"]
      and "g_broken.py::plain" not in d_na["passed"])
    t("N/A 条目带理由进 JSON，且仍记录它**实跑**的真实退出码",
      len(d_na["na"]) == 1 and d_na["na"][0]["id"] == "g_broken.py::plain"
      and d_na["na"][0]["code"] == 2 and d_na["na"][0]["reason"])
    t("N/A 条目仍出现在 exit 明细里（跑了就必须留痕）",
      d_na["exit"].get("g_broken.py::plain") == 2)
    t("分桶恒等式扩成「通过+差距+坏+不适用==总数」",
      len(d_na["passed"]) + len(d_na["gaps"]) + len(d_na["broken"])
      + len(d_na["na"]) == d_na["total"])
    t("strict 模式下 N/A 失效，那道门禁重新按 exit=2 计入（不是恒豁免）",
      d_na_strict["na"] == [] and d_na_strict["broken"] == ["g_broken.py::plain"])

    # ★ 这条是**首版漏了第二个条件**导致的真实回归，本机实跑才发现：
    # 本机**有**本地开发库 ⇒ `contract_readiness_check` 返回 1 并报出 4 条真实
    # contract 缺口，却被无条件标成 N/A 抹掉 ⇒ 差距从 6 掉到 5，
    # **真实差距凭空消失**。N/A 描述的是「此刻查不了」，不是「这门禁不重要」。
    shutil.rmtree(tmp)
    tmp.mkdir()
    _stub(tmp / "g_gap.py", 1)
    one_gap = [("g_gap.py", "plain", "gap")]
    try:
        CI_INAPPLICABLE.clear()
        CI_INAPPLICABLE["g_gap.py::plain"] = "本机查得了，所以不该豁免"
        _rc_g, d_g = run(tmp, "ci", gates=one_gap)
    finally:
        CI_INAPPLICABLE.clear()
        CI_INAPPLICABLE.update(saved_na)
    t("★ 清单里的门禁**实跑出了结论**时仍按结论计入（不得被 N/A 抹掉）",
      d_g["na"] == [] and d_g["gaps"] == ["g_gap.py::plain"]
      and "g_gap.py::plain" not in d_g["passed"])

    # —— INC-67 回归：同一工具的两种 kind 退出码可以不同，绝不能塌成一个键。
    #    夹具必须**真的造出码不同的两次调用**；用四个互不相同的桩（上一版夹具）
    #    在结构上就碰不到这个分支，所以上一版自检永远发现不了它。
    shutil.rmtree(tmp)
    tmp.mkdir()
    (tmp / "g_two.py").write_text(
        "import sys\n"
        "sys.exit(0 if '--self-test' in sys.argv else 1)\n", encoding="utf-8")
    two_kinds = [("g_two.py", "self-test", "阳性对照"),
                 ("g_two.py", "plain", "实跑")]
    rc_two, d_two = run(tmp, "ci", gates=two_kinds)
    t("同一工具的两种 kind 在 exit 里各自留痕（未塌缩成一个键）",
      d_two is not None and
      set(d_two["exit"]) == {"g_two.py::self-test", "g_two.py::plain"} and
      d_two["exit"]["g_two.py::self-test"] == 0 and
      d_two["exit"]["g_two.py::plain"] == 1)
    t("exit 键数 == total（未静默丢弃任何门禁）",
      d_two is not None and len(d_two["exit"]) == d_two["total"] == 2)
    t("passed / gaps 用唯一身份列出，两者不共名",
      d_two is not None and d_two["passed"] == ["g_two.py::self-test"] and
      d_two["gaps"] == ["g_two.py::plain"])
    t("details 带 id，可与 exit 逐条对齐",
      d_two is not None and
      [x["id"] for x in d_two["details"]] == list(d_two["exit"]))

    # 清单里重复列出同一个 (工具, kind) 时，必须报「工具不可运行」而不是静默塌缩
    rc_dup, d_dup = run(tmp, "ci", gates=[("g_two.py", "plain", "a"),
                                          ("g_two.py", "plain", "b")])
    t("清单出现重复门禁身份 → 判工具不可运行，且不产出半份报告",
      rc_dup == EXIT_TOOL_BROKEN and d_dup is None)

    # —— INC-68 回归：输入在运行期间被改写。
    #    端到端证明，不是只测 drifted() 这个纯函数：用一个**自己会去改输入**的
    #    桩门禁，真刀真枪地在快照与复核之间动文件。
    shutil.rmtree(tmp)
    tmp.mkdir()
    (tmp / "product").mkdir()
    (tmp / "product" / "a.md").write_text("# 原始\n", encoding="utf-8")
    _stub(tmp / "g_ok.py", 0)
    t("无改写时 input_drift 为空",
      run(tmp, "ci", gates=[("g_ok.py", "plain", "ok")])[1]["input_drift"] == [])

    (tmp / "g_mutator.py").write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path(__file__).parent / 'product' / 'a.md'\n"
        "p.write_text(p.read_text(encoding='utf-8') + '漂移\\n', encoding='utf-8')\n"
        "sys.exit(0)\n", encoding="utf-8")
    rc_drift, d_drift = run(tmp, "ci", gates=[("g_mutator.py", "plain", "改输入")])
    t("门禁自己改了「设计真相」文件 → ci 模式报 2（结果不可作为证据）",
      rc_drift == EXIT_TOOL_BROKEN)
    t("漂移的文件被点名，不是只说「有问题」",
      d_drift is not None and d_drift["input_drift"] == ["product/a.md"])
    rc_drift_r, d_drift_r = run(tmp, "report", gates=[("g_mutator.py", "plain", "改输入")])
    t("report 模式仍退出 0，但漂移写进报告供取证",
      rc_drift_r == EXIT_OK and d_drift_r["input_drift"] == ["product/a.md"])

    # 自检全程只在自己的临时目录里动作：真实 GATES 未被改写
    t("自检未改写真实门禁清单", GATES == real_gates)
    t("自检未在临时目录外留下文件",
      sorted(p.name for p in tmp.iterdir())
      == ["g_mutator.py", "g_ok.py", "product"])

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(base, ignore_errors=True)
    print("=" * 78)
    passed = sum(1 for _, ok in results if ok)
    if passed == len(results):
        print(f"{GREEN}自检通过：{passed}/{len(results)}{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：{passed}/{len(results)}{RESET}")
    for label, ok in results:
        if not ok:
            print(f"  ✗ {label}")
    return EXIT_FINDINGS


def main() -> int:
    ap = argparse.ArgumentParser(description="门禁套件统一入口")
    ap.add_argument("--tools-dir", help="门禁工具目录")
    ap.add_argument("--truth-root",
                    help="「设计真相」文件所在目录（默认本文件上溯两级的 docs/）。"
                         "自检用它指向临时目录，避免自检期间真实文档被改动而误报漂移")
    ap.add_argument("--mode", choices=["ci", "strict", "report"], default="ci")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    tools_dir = (pathlib.Path(args.tools_dir) if args.tools_dir
                 else pathlib.Path(__file__).resolve().parent)
    if not tools_dir.is_dir():
        sys.stderr.write(f"门禁目录不存在：{tools_dir}\n")
        return EXIT_TOOL_BROKEN

    results: list[dict] = []
    docs_root = (pathlib.Path(args.truth_root) if args.truth_root
                 else pathlib.Path(__file__).resolve().parents[2])
    before = snapshot_inputs(docs_root)
    for name, kind, desc in GATES:
        tool = tools_dir / name
        if not tool.exists():
            results.append({"name": name, "kind": kind, "desc": desc,
                            "code": EXIT_TOOL_BROKEN, "secs": 0.0,
                            "out": f"工具文件不存在：{tool}"})
            continue
        # ★ INC-122：把**到目前为止**已跑过的门禁结果交给本道门禁。
        # 只有「本道之前跑过」的才传——下游复用的必须是它自己没跑过的，
        # 否则复用会掩盖「同一道门禁跑两次」这件事。
        _prior = {result_key(x["name"], x["kind"]): {"code": x["code"],
                                                    "out": x.get("out", "")}
                  for x in results if x.get("code") is not None}
        # ⚠ **自检一律不传**（实测踩到）：自检的职责是**在变异过的夹具上**
        #   证明判据抓得住错，而复用给的是「未变异状态下刚跑出来的结果」。
        #   两者对同一批 (工具, flags) 给出不同答案 ⇒ 复用直接污染夹具基线。
        #   实测：`design_doc_check --self-test` 在套件里 41/52，
        #   单独跑是 52/52 ⇒ **11 条 FAIL 全是复用造成的假红**。
        #   自检慢是它该付的代价：阳性对照必须真的跑一遍。
        #   复用的目标从来是「plain 这类重复执行」，
        #   而**不是**「让自检也变快」——后者是用判据的强度换时间。
        if kind == "self-test":
            _prior = {}
        r = run_gate(tool, kind,
                     env_extra={RESULT_VAR: json.dumps(_prior, ensure_ascii=False)}
                     if _prior else None)
        r.update({"name": name, "kind": kind, "desc": desc})
        # ★ INC-94：`--mode ci` 下声明「本模式不适用」的门禁，
        # **照常跑、照常记下真实退出码**，只是不计入三态。
        # 跑而不跳是有意的：报告里必须能看见它**为什么**不适用，
        # 跳过就等于把「查不了」伪装成「查过了」。
        #
        # ★★ **两个条件缺一不可**（首版漏了第二个，是我自己造出来的回归）：
        #   ① 在 `CI_INAPPLICABLE` 里
        #   ② **实跑确实没跑成**（`code == EXIT_TOOL_BROKEN`）
        # 只判 ① 会造成：在**有本地开发库的机器上**（本机就是），
        # `contract_readiness_check` 返回 1、报出 4 条真实 contract 缺口，
        # 却被标成 N/A 抹掉 ⇒ 差距从 6 掉到 5，**真实差距凭空消失**。
        # **N/A 描述的是「此刻查不了」这个处境，不是「这门禁不重要」。**
        _gid = f"{name}::{kind}"
        if (args.mode == "ci" and _gid in CI_INAPPLICABLE
                and r["code"] == EXIT_TOOL_BROKEN):
            r["na"] = CI_INAPPLICABLE[_gid]
        results.append(r)

    # —— 判定。**先分桶再计数**，不要用「失败数 = 非零数」——
    #    那样会把「工具坏了」和「检查没过」压成同一个数字。
    #
    # ★ INC-94：带 `na` 的条目**不进任何一个桶**，但**仍计入 `total`**。
    # 恒等式因此变成 `通过 + 差距 + 不可运行 + 不适用 == 总数`——
    # **多了一个桶而不是少了一个码**，所以「有码被吞」依然会被 assert 抓住。
    na = [r for r in results if "na" in r]
    judged = [r for r in results if "na" not in r]
    passed = [r for r in judged if r["code"] == 0]
    gaps = [r for r in judged if r["code"] == 1]
    broken = [r for r in judged if r["code"] == 2]
    total = len(results)
    # 唯一必须成立的恒等式：分桶之和 == 总数。少一个就说明有码被吞了。
    assert (len(passed) + len(gaps) + len(broken) + len(na)
            == total), "分桶与总数不一致"

    # —— 第二个恒等式：**门禁身份必须唯一**（INC-67）。
    #    清单里若出现两条同 (工具, kind)，按身份做键也会静默塌缩。
    #    这属于「门禁自己坏了」，必须以 2 报出——不能让它混进「未通过」，
    #    否则使用方会以为门禁正常工作、只是差距还在。
    ids = [gate_id(r) for r in results]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        sys.stderr.write(
            f"门禁清单存在重复身份，观测会被静默丢弃：{dup}\n"
            f"（清单共 {total} 条，去重后 {len(set(ids))} 条）\n")
        return EXIT_TOOL_BROKEN

    # —— 输入漂移核对（INC-68）。
    #    门禁跑在「设计真相」文件上；若这些文件在运行期间被改写，本次结果就是
    #    **半旧半新的混合物**，报出来的红/绿都不能当证据。实测踩过：
    #    一边跑套件一边改设计文档，design_doc_check 报 GAP，单独重跑却是绿的。
    #    误报的代价和漏报一样大——门禁第一次报假警，第二次就会有人把它关掉。
    moved = drifted(before, snapshot_inputs(docs_root))
    if moved:
        sys.stderr.write(
            "门禁运行期间「设计真相」文件发生变化，本次结果不可作为证据：\n"
            + "".join(f"  · {m}\n" for m in moved))
    else:
        moved = []

    if args.json:
        exit_map = {gate_id(r): r["code"] for r in results}
        # 导出层自己的恒等式：键数必须等于门禁数。塌缩在这里当场暴露，
        # 而不是让下游拿到 total=31 / exit 只有 18 项的报告还看不出来。
        if len(exit_map) != total:
            sys.stderr.write(
                f"JSON 导出的 exit 键数 {len(exit_map)} != 门禁数 {total}\n")
            return EXIT_TOOL_BROKEN
        print(json.dumps({
            "mode": args.mode, "total": total,
            "passed": [gate_id(r) for r in passed],
            "gaps": [gate_id(r) for r in gaps],
            "broken": [gate_id(r) for r in broken],
            "input_drift": moved,
            "exit": exit_map,
            "na": [{"id": gate_id(r), "name": r["name"], "kind": r["kind"],
                    "code": r["code"], "reason": r["na"]} for r in na],
            "details": [{"id": gate_id(r), "name": r["name"], "kind": r["kind"],
                         "code": r["code"], "secs": r["secs"],
                         "na": r.get("na"),
                         "tail": tail(r["out"], 4)} for r in results],
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print(f"门禁套件（mode={args.mode}）")
        print("=" * 78)
        for r in results:
            tag, color, desc = VERDICT[r["code"]]
            print(f"  [{color}{tag:6}{RESET}] {r['name']:28} {DIM}{r['kind']:9}{RESET}"
                  f" {r['secs']:>6.1f}s  {desc}")
            print(f"          {DIM}{tail(r['out'])}{RESET}")
        print("-" * 78)
        print(f"  共 {total} 道：通过 {GREEN}{len(passed)}{RESET}"
              f" · 差距已记录 {YELLOW}{len(gaps)}{RESET}"
              f" · 工具不可运行 {RED}{len(broken)}{RESET}"
              + (f" · 本模式不适用 {DIM}{len(na)}{RESET}" if na else ""))
        if na:
            # ★ 必须**显眼**。把「没查」和「查过且通过」显示成同一个绿色，
            # 就是本项目反复吃过的那种空洞通过。
            print(f"\n  {DIM}本模式不适用（**未判定**，不计入上面三态，"
                  f"也不参与退出码）：{RESET}")
            for r in na:
                print(f"    ○ {r['name']}（{r['kind']}）"
                      f" {DIM}实跑 exit={r['code']}{RESET}")
                print(f"      {DIM}理由：{r['na']}{RESET}")
        if broken:
            print(f"\n  {RED}工具不可运行的门禁（门禁本身坏了，必须修）：{RESET}")
            for r in broken:
                print(f"    ✗ {r['name']}（{r['kind']}）")
        if gaps:
            print(f"\n  {YELLOW}检查未通过的门禁（差距真实且已登记，"
                  f"{'strict 模式下会拦截' if args.mode == 'strict' else 'ci 模式下不拦截'}）：{RESET}")
            for r in gaps:
                print(f"    · {r['name']}（{r['kind']}）")
        if moved:
            print(f"\n  {RED}本次结果不可作为证据{RESET}：运行期间 {len(moved)} 个"
                  f"「设计真相」文件被改写")
            for m in moved:
                print(f"    ⚠ {m}")
        print("=" * 78)

    if moved and args.mode != "report":
        # report 模式只取证不判定，漂移记在报告里就够了（JSON 的 input_drift 字段）。
        # ci / strict 下漂移必须拦：结果是在半旧半新的输入上算出来的。
        return EXIT_TOOL_BROKEN
    if args.mode == "report":
        return EXIT_OK
    if broken:
        return EXIT_TOOL_BROKEN if args.mode == "ci" else EXIT_FINDINGS
    if gaps and args.mode == "strict":
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
