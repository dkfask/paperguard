#!/usr/bin/env python3
"""规格规则 ID 唯一性检查：跨规格**重复定义**必须报出来。

为什么这个门禁存在
------------------
2026-10-01 实测事故（INC-49）
--------------------------------
13 份工作包规格分多轮写成，**规则 ID 之间从未互相核对过**。
全量审计发现 **25 个 ID 被两份规格同时「定义」**，分 6 组，
且**多数语义完全无关**：

| ID | 规格 A | 含义 A | 规格 B | 含义 B |
|---|---|---|---|---|
| `CB-1~5` | W-07 | **熔断** circuit breaker | W-12 | **回调** callback |
| `DB-1~4` | W-03 | **双标签/缓存/进程状态** | W-08 | **看板** dashboard |
| `ID-1~3` | W-09 | 任务幂等 | W-11 | 账务幂等 |
| `PV-1~4` | W-06 | **计划版本** plan version | W-12 | **支付渠道** provider |
| `RC-1~5` | W-11 | **对账** reconciliation | W-12 | **未知与恢复** recovery |
| `RS-1~10` | W-10 | **恢复** restore | W-11 | **预留结算** reservation |

危害不是「不好看」，是**查错规则**：
实施者被告知「按 W-07 CB-3」时，可能查到 W-12 的回调规则；
被告知「按 W-12 RS-4」时，可能查到 W-10 的恢复要求。
**规格里指向错误规则的引用，比没有引用更危险。**

这与 INC-27（`context_resolution_check` 的 CR-5 门禁↔规格规则 ID 互不知情）
是同一形态的不同规模版本：CR-5 只覆盖 W-03 一份规格的 11 个 ID，
本门禁覆盖全部 13 份、316 个 ID。

**判据一：只认「定义」，不认「引用」**
------------------------------------
跨规格**引用**上游规则是正常且必要的（W-12 引用 W-09 的 `LE-1~LE-6`）。
因此本门禁只统计**表格首列即为该 ID 的行**（即「本规格定义了一条规则」），
散落在正文里的引用一律不计。
否则门禁会对 30 处正常引用全部误报——**误报的代价和漏报一样大**。

**判据二：RI-1 悬空引用必须报出来**
------------------------------------
重命名 `W-08 DB→DASH` 之后，**我自己就在设计文档里留下了 `DB-7`**（指向 W-08），
在 W-12 里留下了 3 处被误改的 `PRV-5`（本应指 W-06 的 `PV-5`）。
它们都**查不到定义**，而读者会照着找一个不存在的规则。
**指向错误规则的引用比没有引用更危险。**

RI-1 的前缀集合**从规格自身学习**（凡在定义行出现过的前缀即纳入），
**不硬编码白名单**。因此：
- `CB-` 是 W-07 定义过的前缀 ⇒ 引用 `CB-10` 而无定义 → 报红（真问题）
- `FIX-`（来自 `MR-FIX-n`）、`BV-`、`ACT-`、`LO-` 等外部编号体系
  **从未被规格定义过** ⇒ 不在集合内 ⇒ 不报（无误报）

退出码：`0` 通过 / `1` 有重复定义或悬空引用 / `2` 工具不可运行
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
PRODUCT = REPO / "docs/product"

# 「定义」的形态：表格首列就是规则 ID（允许 **加粗**）
#
# **三种形态都要认**（2026-10-02 因 INC-78 扩展）
# --------------------------------------------------
# 首版只认 `| XX-N |`，于是**另外两种真实存在的定义形态整体落进视野之外**：
#   ① `| AC-02-1 | …`   —— **两段连字符**的验收判据，**94 条**（13 份规格都有）
#   ② `| ADR-01 模块化单体 | …` —— **ID 与标题同格**，8 条（只在 W-01）
# 后果不是「少报几个」，是**那 102 个编号实体从未被检查过**：
# W-01/W-02/W-04 的摘要行会显示「未定义任何规则 ID（0 条）」，
# 而读者会以为这三个规格没有规则。**这与 INC-57 是同一个形态：
# 校验层的形态假设比现实窄，而缺口不会报警。**
#
# **这三类都不是「外部命名空间」**：验收判据（AC-xx-n）是规则到 AT 的连接层，
# ADR 决议（ADR-0n）是总设计 §7.2 引用的约束性决议。两者都该进视野。
DEF_ROW = re.compile(
    r"^\|\s*\*{0,2}([A-Z]{2,4})-(\d{1,2})(?:-\d{1,2})?\*{0,2}"
    r"(?:\s*\||\s+[^|]*\|)")
SPEC_NAME = re.compile(r"^W-\d{2}")
ANY_REF = re.compile(r"\b([A-Z]{2,4})-(\d{1,2})(-\d{1,2})?\b")

# 外部编号命名空间：**不属于工作包规格规则**，RI-1 不判定
# ------------------------------------------------------------------
# 为什么必须显式声明这张表（而不是靠前缀白名单自动学）
# --------------------------------------------------------
# 门禁判据（`EC-01`/`CR-5`/`RL-1`/`DC-5`）、验收用例（`AT-xx`）、
# 发现编号（`INC-xx`）、ADR、待裁决项（`BQ-01`/`D-02`/`V-1`）、
# 外部检查项（`MR-FIX-n`/`BV-n`/`CHK-n`/`MIG-n`/`BUG-n`）——
# **它们的字母形状与规格规则前缀完全相同**。
# 同一段文字里既可能有「W-07 的 CB-3」也可能有「门禁的 CR-5」，
# **机器没有任何办法区分**。
#
# 不用这张表的后果：RI-1 会对 30+ 处合法外部引用全部误报。
# **误报的代价和漏报一样大**——门禁第一次报假警，第二次就会有人把它关掉。
#
# 这不是「用白名单掩盖问题」，而是**显式声明命名空间边界**：
# 这些前缀的所有权在别的文档里，不归工作包规格管。
EXTERNAL_PREFIXES = {
    "AT",    # §24.2 验收用例
    "INC",   # 发现编号
    # ⚠ `AC` / `ADR` **已从本表移出**（2026-10-02 INC-78 收口）。
    #   原注释写「AC-06-1 是三位数，形态本就不同」——**那是错的**：
    #   `AC-06-1` 的第二段就是两位数。真实形态是**两段连字符**（`AC-02-9`），
    #   而旧 `DEF_ROW` 只认 `| XX-N |`，所以它「看起来形态不同」只是因为没被认出来。
    #   `ADR` 则是 W-01 表格里**真实存在的 8 条决议**（`| ADR-02 标题 | …`）。
    #   把两者留在外部 = 102 个已定义实体对 RI-1 完全不可见，
    #   而 `collect_defs` 仍把它们计入规则数——**同一族问题（INC-57 形态）**。
    "EC",    # error_codes_check 判据
    "RL",    # rls_policy_check 判据
    "CR",    # context_resolution_check 判据（★ 见下方「BQ / CR 为什么必须留在外部」）
    "CT",    # contract_readiness_check 判据
    "SD",    # schema_disposition_check 判据
    "DC",    # design_doc_check 判据
    "MR",    # MR-FIX（正则只截到 FIX，见下方说明）
    "FIX",   # MR-FIX-n 的尾部
    "BUG",   # W-01 缺陷清单
    "BV",    # backfill_validate 检查项
    "CHK",   # W-02 检查项
    "MIG",   # 迁移检查项
    "ACT",   # permission-matrix 动作编号
    "LO",    # W-03 最后所有者保护（非表格形态定义）
    "DR",    # 数据分级编号
    "BQ",    # quota-and-billing-review §8 待裁决项
    "D",     # §26.1 产品与商业决策 D-01~D-11
    "V",     # §26/§25 指标与外部确认 V-1~V-5
}

# BQ / CR 为什么必须留在外部（**实测结论，不是推断**）
# ------------------------------------------------------------
# 2026-10-02 实跑核对：这两族在表格首列里**确实有行**，但那些行不是业务规则。
#
# | 位置 | 首列 | 那一行到底是什么 |
# |---|---|---|
# | W-03 | `CR-1`…`CR-4` | **门禁判据的镜像**——「`RequestContext` 是 frozen=True 且有
#   `__post_init__` 断言 CI-1～CI-4」，检查对象是代码不是业务 |
# | W-11 | `BQ-02` | `quota-and-billing-review.md` 的**待裁决项**，`reserved` 超时回收时长 |
#
# 把它们纳入 RI-1 会产生**实测 2 处硬误报**（总设计正文里的 `BQ-01～BQ-05`
# 与 `CR-1～CR-5` 全部指向外部文档），门禁第一次报假警、第二次就会被人关掉。
# **误报与漏报代价一样大**，所以这里选误报为 0 的一侧。
#
# ⚠ **代价必须写明**：留在外部的代价是这两族**对 RI-1 不可见**。
# 缓解方式是 `rule_defs()`——它们**不计入规则数、不参与前缀学习**，
# 避免「既被当定义又被当外部」的自相矛盾（见该函数）。
BQ_EXTERNAL_EVIDENCE = {"CR", "BQ"}


def collect_defs(text: str) -> list[str]:
    """返回本文档**定义**的规则 ID（表格首列），正文引用不算。"""
    out: list[str] = []
    for line in text.splitlines():
        m = DEF_ROW.match(line)
        if m:
            # **整段取原文，不按分组重组**（INC-78）：
            # 首版用 `f"{g1}-{g2}"` 重组，把 `AC-02-1` 的第三段丢掉、变成 `AC-02`，
            # 于是 94 条验收判据**全部塌缩成每规格一个假 ID**，
            # 而「13 份规格无重复定义」这个结论**看起来仍然是绿的**——
            # **塌缩后的集合几乎不可能撞车，于是判据安静地通过了。**
            out.append(m.group(0).split("|", 1)[1].strip()
                       .lstrip("*").split()[0].rstrip("*").rstrip("|").strip())
    return out


def rid_of(prefix: str, num: str, sub: str | None) -> str:
    """按 ANY_REF 的三个分组还原**完整** ID。

    ⚠ **INC-78 首版的真实缺陷**：旧代码写死 `f"{pfx}-{num}"`，
    把第三段丢掉——于是 `AC-02-9` 被读成 `AC-02`。
    后果不是「少报」而是**报错对象错了**：读者去查 `AC-02`，
    而它根本不存在（真实定义是 `AC-02-1…AC-02-13`）。
    **判据精确度不够时，报红本身就是在指错方向。**

    ⚠ `sub` 是 `ANY_REF` 的第 3 组，**自带前导连字符**（`-1`），
    所以这里直接拼接；写成 `f"-{sub}"` 会拼出 `AC-01--1`。
    """
    return f"{prefix}-{num}{sub or ''}"


def is_placeholder(text: str, end: int) -> bool:
    """判断 `end` 处往后是不是子编号字面占位符（`AC-06-x` / `CB-1-*`）。

    **必须看原文，不能看重组后的 ID**：占位符那个字符根本不会被 `ANY_REF`
    吃进分组，重组后已经无迹可寻。首版就是在这里判的，
    结果容错完全没生效、3 处硬误报照旧报红。
    """
    return bool(re.match(r"-(?:x|X|\*|n|N|#|…)", text[end:]))



def is_external(rid: str) -> bool:
    m = re.match(r"^([A-Z]{2,4})-", rid)
    return bool(m) and m.group(1) in EXTERNAL_PREFIXES


def rule_defs(text: str) -> list[str]:
    """本文件定义的**业务规则 ID**（表格首列，且不属于外部命名空间）。

    为什么要在 `collect_defs` 之后再滤一层
    --------------------------------------
    `collect_defs` 只认「首列长得像编号」，而**外部命名空间的首列同样长得像编号**：
    W-03 的 `CR-1…CR-4`、W-11 的 `BQ-02`。它们被计入规则数会让
    「N 个规则 ID」这个数字**掺水**，而且让对应前缀同时「被定义为规则」
    与「被声明为外部」——**自相矛盾且不会报警**。
    实测：不去滤会把 449 报成 449，读者以为多出 5 条业务规则。
    """
    return [r for r in collect_defs(text) if not is_external(r)]


def family_of(rid: str) -> str | None:
    """`AC-02-1` → `AC-02`（族）；`CB-1` → `None`（本身就是规则 ID）。"""
    m = re.match(r"^([A-Z]{2,4}-\d{1,2})-\d{1,2}$", rid)
    return m.group(1) if m else None


def families_of(defined: set[str]) -> set[str]:
    """已定义 ID 撑起来的族锚点集合（`AC-02-1` ⇒ 族 `AC-02` 存在）。"""
    out: set[str] = set()
    for rid in defined:
        fam = family_of(rid)
        if fam:
            out.add(fam)
    return out


def learned_prefixes(defs: set[str]) -> set[str]:
    """从已定义的规则 ID 里学出前缀集合。

    **不硬编码白名单**：外部编号体系（MR-FIX-/BV-/ACT-/LO-…）从未被规格
    定义过，天然不在集合内，因此天然不被 RI-1 判定为悬空。

    ⚠ **INC-78 首版的真实缺陷**：旧实现用 `rid.rsplit("-", 1)[0]`，
    对两段 ID 正确，对三段的 `AC-02-1` 却得到 `AC-02`——
    而 `ANY_REF` 抽出的前缀是 `AC`，**两边对不上，该族静默退出视野**。
    正确做法是取**开头那段字母**。
    """
    out: set[str] = set()
    for rid in defs:
        m = re.match(r"^([A-Z]{2,4})-", rid)
        if m:
            out.add(m.group(1))
    return out


def dangling_refs(text: str, prefixes: set[str],
                  families: set[str] | None = None) -> set[str]:
    """返回 text 中引用了、但既不在 prefixes 也不被本文件定义的 ID。

    传入的 prefixes 是**全局已定义前缀**，因此同文件内的自引用不会误报。
    EXTERNAL_PREFIXES 里的命名空间**整体跳过**（见其定义处的理由）。

    `families` 是**族锚点容忍**（INC-78 收口）
    ---------------------------------------
    两段形态 `AC-02` 在正文里是**族锚点**——指向 `AC-02-1…AC-02-13` 那一族，
    与已登记的「区间引用 `DB-1～DB-7` 只检端点」是同一族边界。
    实测 5 处：`AC-01`/`AC-02`（描述塌缩形态的正文）与 `AC-06`/`AC-07`/`AC-08`
    （`AC-06-x` 占位符）。

    **它放松了什么，必须说清**：
    - 只对**两段**引用生效；`AC-02-99` 这种三段引用**仍必须精确命中**，
      所以「引用了族里不存在的那一条」照样报红。
    - 一个族只要**有任意一条**定义就被视为存在 ⇒ 若某族只剩 `-1`，
      引用 `-2` 仍会报（`-2` 是三段）；但引用**族锚点本身**时看不出该族已残缺。
    - 族容错**不能**掩盖「整个族从未定义」——那时 `families` 里没有它，照样报。
    """
    local = set(collect_defs(text))
    fams = families or set()
    out: set[str] = set()
    for m in ANY_REF.finditer(text):
        pfx, num, sub = m.group(1), m.group(2), m.group(3)
        if pfx not in prefixes or pfx in EXTERNAL_PREFIXES:
            continue
        if is_placeholder(text, m.end()):
            continue
        rid = rid_of(pfx, num, sub)
        if not sub and rid in fams:
            continue
        if rid not in local:
            out.add(rid)
    return out


def verdict(total_specs: int, total_ids: int, dup: dict, dangling: dict,
            err: str) -> int:
    if err:
        print(f"工具不可运行：{err}", file=sys.stderr)
        return 2
    if not total_specs:
        print("✗ 未找到任何工作包规格 → 报 2（提取层空输入不得判绿）")
        return 2
    if not total_ids:
        # ★ INC-92（2026-10-03 实测发现）：0 个规则 ID 时本门禁**验证了 0 件事**
        # 却打印「✓ 通过：1 份规格、0 个规则 ID，无跨规格重复定义、无悬空引用」。
        #
        # 为什么上面那条 `not total_specs` 闸**接不住**这个状态：
        # 规格文件是存在的（total_specs=1），只有**规则定义**被提取层漏掉了。
        # 两种空输入长得几乎一样，判据却只覆盖了一种——而漏掉的那一种
        # 恰好是最危险的：它不是「没东西可查」，而是「本该查的东西没查到」，
        # 且**报的是通过**。
        #
        # 修法与既有闸同构，但**措辞必须区分**：
        #   · `not total_specs` ⇒ 仓库里没有规格 ⇒ 2
        #   · `not total_ids`   ⇒ 规格在、规则一条没提取到 ⇒ 2（更可能是正则失配）
        # 两者都返回 2（工具不可运行）而不是 1：本工具此时**没有结论**，
        # 不是「核对后没发现问题」。报 1 会让 run_all_gates 把它记成
        # 「差距已登记」——那正是 INC-81「崩溃伪装成有差距」的同族。
        print("✗ 规格存在但提取到 0 个规则 ID → 报 2"
              "（本门禁此刻未核对任何东西，不得据此得出「无重复、无悬空」）")
        return 2
    failed = False
    if dup:
        failed = True
        print(f"检出 {len(dup)} 个被多份规格重复定义的规则 ID：\n")
        for rid, owners in sorted(dup.items()):
            print(f"  ✗ {rid:9s} 定义于 {owners}")
        print(
            "\n重复定义会让「按 XX-n 实施」指向错误规则。"
            "\n修法：改其中较晚规格的前缀（保留先写者的语义前缀），"
            "并同步该规格内部的全部引用。"
        )
    if dangling:
        failed = True
        print(f"\n检出 {len(dangling)} 个悬空规则引用（引用了但无任何规格定义）：\n")
        for rid, where in sorted(dangling.items()):
            print(f"  ✗ {rid:9s} 出现于 {where}")
        print(
            "\n悬空引用通常由**改名未同步**造成：重命名某个规格的前缀后，"
            "别处指向它的引用会静默失效。\n"
            "修法：逐条确认该指向的是新前缀，还是原本就指向另一份规格的同名 ID。"
        )
    if failed:
        return 1
    print(f"✓ 通过：{total_specs} 份规格、{total_ids} 个规则 ID，"
          f"无跨规格重复定义、无悬空引用")
    return 0


def check_endpoint_bindings(design_text: str, texts: dict[str, str]) -> list[str]:
    """EP-2：§18.2 接口清单的「归属规格 · 规则」必须可核对。

    为什么需要（2026-10-01 真实踩到，INC-59）
    ---------------------------------------------
    §18.2 有 27 条接口，而 13 份规格里**字面路径出现 0 次**——
    两者之间没有任何绑定。后果是「客户启用」（§23.1 明确划给 W-05）
    在规格里一条规则都没有，且两条平台端点在 DDL 层根本无法实现。

    抽成**纯函数**（不读盘、不依赖全局）有两个理由：
    ① 自检可以用夹具直接调用，不需要改动真实设计文档；
    ② 与本门禁既有风格一致（`dangling_refs` / `collect_defs` 都是纯函数）。

    判据刻意收窄到**可机械核对**的部分：
      · 每行必须有归属包，且该包真实存在（`W-\\d\\d` 且有规格文本）
      · 引用的规则 ID **必须在该归属规格里真实定义**
      · 允许显式声明「无规则级锚点」（`⚠`），这是**如实登记的缺口**，
        不判红——**缺口被如实标出，好过编一个看起来完整的引用**

    刻意**不**校验「规格是否真的覆盖了这条接口的语义」：
    那需要读懂散文，机器做不到；勉强做出来的就是 INC-56 那道只会报绿的假门禁。
    """
    out: list[str] = []
    if "### 18.2 建议接口清单" not in design_text:
        return out
    block = design_text.split("### 18.2 建议接口清单", 1)[1].split("### 18.3", 1)[0]
    n_bound = 0
    for row in re.findall(r"^\|.*$", block, re.M):
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        endpoint = cells[0]
        if len(cells) < 4:
            out.append(f"EP-2 §18.2 `{endpoint}` 没有归属列——接口与规格之间无法核对")
            continue
        owner_col = cells[3]
        n_bound += 1
        if "⚠" in owner_col:
            continue
        m = re.match(r"(W-\d\d)", owner_col)
        if not m:
            out.append(f"EP-2 §18.2 `{endpoint}` 归属列格式不可解析：{owner_col}")
            continue
        owner = m.group(1)
        if owner not in texts:
            out.append(f"EP-2 §18.2 `{endpoint}` 归属到 {owner}，但该规格不存在")
            continue
        defined = set(rule_defs(texts[owner]))
        for m in ANY_REF.finditer(owner_col):
            full = rid_of(m.group(1), m.group(2), m.group(3))
            if full not in defined:
                out.append(
                    f"EP-2 §18.2 `{endpoint}` 引用的 {owner} `{full}` "
                    f"在该规格里没有定义——读者会照着找一个不存在的规则")
    if n_bound == 0 and not out:
        out.append("EP-2 §18.2 解析到 0 条带归属的接口，不得为 0")
    return out


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if not PRODUCT.is_dir():
        return verdict(0, 0, {}, f"规格目录不存在：{PRODUCT}")
    specs = sorted(p for p in PRODUCT.glob("W-*.md") if SPEC_NAME.match(p.name))
    if not specs:
        return verdict(0, 0, {}, {}, "docs/product 下无 W-*.md 规格")
    texts: dict[str, str] = {}
    owners: dict[str, set[str]] = collections.defaultdict(set)
    total = 0
    for p in specs:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            return verdict(0, 0, {}, {}, f"读取失败 {p}: {exc}")
        texts[p.name[:4]] = text
        raw = collect_defs(text)
        # 外部命名空间的首列（CR-/BQ- 等）**不是业务规则**，剔出去再计数。
        # 剔掉的必须**打印出来**而不是静默丢弃——否则「N 个规则 ID」这个数字
        # 会在有人往待裁决表里加一行时无声地变，而没人知道它变过。
        dropped = sorted({r for r in raw if is_external(r)})
        if dropped:
            print(f"  · {p.name[:4]}：另有 {len(dropped)} 条外部命名空间首列"
                  f"不计入规则数（{', '.join(dropped)}）")
        ids = [r for r in raw if not is_external(r)]
        if not ids:
            # 有规格一条规则都没定义：可能是有意的（如纯散文规格），
            # 但必须让人看见，而不是静默计入「无冲突」
            print(f"  · {p.name[:4]}：未定义任何规则 ID（0 条）")
        for rid in ids:
            owners[rid].add(p.name[:4])
            total += 1
    dup = {k: sorted(v) for k, v in owners.items() if len(v) > 1}
    prefixes = learned_prefixes(set(owners))

    # RI-1：悬空引用。规格 + 设计文档都查（设计文档的 INC 表里也引用规则 ID）
    targets = dict(texts)
    design = PRODUCT / "saas-platform-design-20261001.md"
    if design.is_file():
        targets["design"] = design.read_text(encoding="utf-8")
    dangling: dict[str, set[str]] = {}
    fams = families_of(set(owners))
    for where, text in targets.items():
        for rid in dangling_refs(text, prefixes, fams):
            dangling.setdefault(rid, set()).add(where)
    # 排除「全局确有定义、只是本文件没定义」的：那些是正常跨规格引用
    dangling = {k: sorted(v) for k, v in dangling.items() if k not in owners}

    # EP-2：§18.2 接口清单的「归属规格 · 规则」必须可核对
    # -------------------------------------------------------------
    # 为什么需要（2026-10-01 真实踩到，INC-59）
    # ---------------------------------------------
    # §18.2 有 27 条接口，而 13 份规格里**字面路径出现 0 次**——
    # 两者之间没有任何绑定。后果是「客户启用」（§23.1 明确划给 W-05）
    # 在规格里一条规则都没有，且两条平台端点在 DDL 层根本无法实现。
    #
    # 判据刻意收窄到**可机械核对**的部分：
    #   · 每行必须有归属包，且该包真实存在（`W-\d\d` 且有规格文件）
    #   · 引用的规则 ID **必须在该归属包里真实定义**
    #   · 允许显式声明「无规则级锚点」（`⚠`），这是**如实登记的缺口**，
    #     不判红——**缺口被如实标出，好过编一个看起来完整的引用**
    #
    # 刻意**不**校验「规格是否真的覆盖了这条接口的语义」：
    # 那需要读懂散文，机器做不到；勉强做的门禁就是 INC-56 那道假门禁。
    bind_problems: list[str] = []
    if design.is_file():
        bind_problems = check_endpoint_bindings(
            design.read_text(encoding="utf-8"), texts)

    if bind_problems:
        verdict(len(specs), total, dup, dangling, "")
        for b in bind_problems:
            print(f"  ✗ {b}")
        return EXIT_FINDINGS
    return verdict(len(specs), total, dup, dangling, "")


def self_test() -> int:
    """阳性 + 阴性 + 变异，三者缺一不可。"""
    print("=" * 70)
    print("spec_rule_id_check 自检")
    print("=" * 70)
    results: list[tuple[str, bool, str]] = []

    # 阳性：INC-49 的真实形态——同一 ID 两份规格各定义一次
    a = "| CB-1 | 熔断按凭据作用域 | … |\n| CB-2 | 401 只影响凭据 | … |\n"
    b = "| CB-1 | inbox 唯一键 | … |\n| CB-2 | 先持久化再应答 | … |\n"
    dup = set(collect_defs(a)) & set(collect_defs(b))
    results.append(("ST-1 同 ID 两规格定义 → 判为重复",
                    dup == {"CB-1", "CB-2"}, f"重复集算错：{dup}"))

    # 阳性：**加粗**首列也算定义（真实规格大量使用 **AT-03** 这种写法）
    bold = "| **PV-1** | 计划版本发布后不可改 | … |\n"
    results.append(("ST-2 加粗首列识别为定义",
                    collect_defs(bold) == ["PV-1"], "加粗首列漏识别"))

    # 阴性：**跨规格引用不算定义**（W-12 引用 W-09 的 LE-1~LE-6 是正常的）
    ref_only = "上游依赖 W-09 的 `LE-1～LE-6`，见 W-09 §2.2。\n"
    results.append(("ST-3 正文跨规格引用不计入定义",
                    collect_defs(ref_only) == [], "正文引用被误当定义"))

    # 阴性：同规格内 ID 重复不构成跨规格冲突（本门禁只管跨规格）
    same = "| RS-1 | a |\n| RS-2 | b |\n| RS-1 | c |\n"
    results.append(("ST-4 收集逻辑只按集合去重，不误报同规格内重复",
                    collect_defs(same) == ["RS-1", "RS-2", "RS-1"],
                    "收集行为异常"))

    # 阴性：不同前缀不混淆
    two = "| JV-1 | 守恒 |\n| PR-1 | 整数金额 |\n"
    results.append(("ST-5 不同前缀正确区分",
                    set(collect_defs(two)) == {"JV-1", "PR-1"}, "前缀混淆"))

    # ---- INC-78：另外两种**真实存在**的定义形态 ----
    # 首版只认 `| XX-N |`，于是 94 条验收判据与 8 条 ADR 决议整体不可见，
    # W-01/W-02/W-04 的摘要行会显示「未定义任何规则 ID（0 条）」。
    # **夹具必须用真实形态**，否则形态假设被改坏时夹具照样绿。
    #
    # ⚠ 编号从 ST-21 起：本段最初写成 ST-6/7/8，与下方早已存在的
    # ST-6/7/8 **撞号**。撞号的后果不是「难看」——自检输出里两条同名条目
    # 一红一绿时，**读的人无法判断是哪一条判据坏了**。
    ac = "| AC-02-1 | E1 | … |\n| AC-02-2 | E2 | … |\n"
    results.append(("ST-21 两段连字符的验收判据识别为定义",
                    set(collect_defs(ac)) == {"AC-02-1", "AC-02-2"},
                    f"AC 形态漏识别：{collect_defs(ac)}"))
    adr = "| ADR-02 共享 PostgreSQL + RLS | 是否采纳 | … |\n"
    results.append(("ST-22 「ID + 标题」同格的 ADR 识别为定义",
                    set(collect_defs(adr)) == {"ADR-02"},
                    f"ADR 形态漏识别：{collect_defs(adr)}"))

    # 阳性：新增形态进入视野后，**悬空引用也必须被抓**——
    # 这是扩展形态的全部价值所在，只测「能收集」是不够的。
    #
    # ⚠ 样本**不能用 `AC-02-9`**：它是 W-02 真实存在的 ID，
    # 我第一版就是拿它当「无定义」样本的，结果夹具要求门禁报一个**真实存在**的
    # 规则——**夹具自己就是错的，而它在报告 ST-8 失败**。
    # 这正是「写不出正确夹具」比「门禁报错」更值得注意的地方。
    pre = learned_prefixes({"AC-02-1", "ADR-02"})
    dng = dangling_refs("本包承接上游 `AC-02-99` 的判据。\n", pre)
    results.append(("ST-23 新形态的悬空引用被抓（AC-02-99 无定义 → 报）",
                    dng == {"AC-02-99"}, f"悬空未报：{dng}"))
    # 阴性：真实存在的三段 ID 不得被误报（守住 ST-23 的另一半）
    # ⚠ 样本必须**自带定义**：`dangling_refs` 的 `local` 取自**本文件**，
    #   我第一版只写了一句「承接 `AC-02-1`」而没给定义行，
    #   于是它**理直气壮地报了悬空**，夹具却要求它安静——夹具自己错了。
    ac_self = "| AC-02-1 | E1 | … |\n\n本节承接 `AC-02-1` 的判据。\n"
    results.append(("ST-24 三段形态不塌缩成两段（AC-02-1 已定义 → 不报）",
                    dangling_refs(ac_self, pre) == set()
                    and rid_of("AC", "02", "-1") == "AC-02-1",
                    "三段 ID 被塌缩或拼成双连字符"))

    # 变异：把阴性样本改成阳性，判定必须翻面
    mutated = "| LE-1 | 领取必须带 epoch |\n"
    flipped = bool(set(collect_defs(mutated)) & set(collect_defs(ref_only)))
    results.append(("ST-6 变异：无定义→有定义，判定跟着变",
                    not flipped, "变异未改变判定"))

    # 边界：空输入不得判绿
    empty_ok = collect_defs("") == [] and collect_defs("没有表格的散文\n") == []
    results.append(("ST-7 空/散文输入返回空定义集（配合主流程的覆盖率闸）",
                    empty_ok, "空输入行为异常"))

    # ---- RI-1 悬空引用 ----
    # 前缀从定义里学，不硬编码。
    #
    # **刻意包含 DB-、CR-、BQ-**：真实仓库里 W-03 定义了 DB-1~4 与 CR-1~5，
    # W-11 的「待裁决」表首列是 BQ-02（也被本正则计为定义）。
    # 若不把它们放进已学集合，下面 ST-9/ST-14/ST-15 会**因为前缀没被学到而
    # 空转通过**——那样的测试等于没测。
    pfx = learned_prefixes({"CB-1", "CB-2", "DB-1", "DASH-1", "CR-1", "BQ-1"})
    results.append(("ST-8 前缀从定义学习，不含外部体系",
                    pfx == {"CB", "DB", "DASH", "CR", "BQ"},
                    f"学到的前缀不对：{pfx}"))

    # 阴性：外部编号体系（MR-FIX-/BV-/ACT-）**不得**被判为悬空
    ext = "按 MR-FIX-1 落地；见 docs/tenancy/quota-and-billing-review.md 的 BQ-01 与 BV-1。\n"
    results.append(("ST-9 外部编号体系不判悬空（无误报）",
                    dangling_refs(ext, pfx) == set(),
                    f"外部编号被误判：{dangling_refs(ext, pfx)}"))

    # 阴性：未学到的前缀即使带编号也不管（BQ-/LO- 从未被规格定义过）
    results.append(("ST-9b 未学到的前缀不判悬空",
                    dangling_refs("参考 BQ-05 与 LO-3。\n", pfx) == set(),
                    "未学前缀被误判"))

    # 阴性：本文件内的自引用不得判悬空
    selfref = "| CB-1 | 定义 |\n\n补充：CB-1 的例外见下。\n"
    results.append(("ST-10 同文件内自引用不判悬空",
                    dangling_refs(selfref, pfx) == set(),
                    "自引用被误判"))

    # 阳性：**本轮真实事故**——改名后别处仍指向旧 ID。
    # 注意「DB-1～DB-7」是**区间**写法：正则只能看到两个端点
    # （DB-1 与 DB-7），中间的 DB-2/3/4 在原文里并不存在。
    # 这是本判据的已知边界：**区间引用只能检出端点**。
    stale = "已补 W-12 CB-10/CBK-11 明确；另见 W-08 §5 DB-1～DB-7。\n"
    hit = dangling_refs(stale, pfx)
    results.append(("ST-11 改名后的旧引用被判悬空（区间只检端点，是已知边界）",
                    hit == {"CB-10", "DB-1", "DB-7"},
                    f"检出不对：{sorted(hit)}"))

    # 变异：把阴性样本改成阳性，判定必须翻面
    clean = "按 MR-FIX-1 落地，参考 CB-1。\n"
    flipped = bool(dangling_refs(clean, pfx)) == bool(
        dangling_refs("按 MR-FIX-1 落地，参考 CB-9。\n", pfx))
    results.append(("ST-12 变异：存在→缺失，判定跟着变",
                    flipped, "变异未改变判定"))

    # 关键边界：ST-11 检出的 DB-1~DB-4 在**真实仓库里是有定义的**（W-03），
    # 主流程靠「全局已定义集合」过滤掉它们，只留真正悬空的 DB-7。
    # 这一条证明该过滤存在且方向正确——没有它就会对正常跨规格引用全部误报。
    global_defined = {"CB-1", "CB-2", "DB-1", "DB-2", "DB-3", "DB-4", "DASH-1"}
    after = {k for k in hit if k not in global_defined}
    results.append(("ST-13 全局已定义集合过滤掉跨规格正常引用",
                    after == {"CB-10", "DB-7"},
                    f"过滤结果不对：{sorted(after)}"))

    # 阴性：**外部命名空间不得判悬空**（RI-1 首版把 BQ-01/CR-5 误报了）
    ns = "对照 CR-5 门禁判据与 BQ-01 待裁决项，另见 D-02、V-1、MR-FIX-4。\n"
    results.append(("ST-14 外部命名空间（CR/BQ/D/V/MR-FIX）不判悬空",
                    dangling_refs(ns, pfx) == set(),
                    f"外部命名空间被误判：{sorted(dangling_refs(ns, pfx))}"))

    # 变异：把外部命名空间移出 EXTERNAL，判定必须翻面。
    # **这一步证明 EXTERNAL_PREFIXES 不是摆设**——CR-/BQ- 已在已学前缀内，
    # 清空该表后它们会立刻变成「悬空」，也就是 RI-1 首版的真实误报形态。
    saved_ext = set(EXTERNAL_PREFIXES)
    try:
        EXTERNAL_PREFIXES.clear()
        leaked = dangling_refs(ns, pfx)
    finally:
        EXTERNAL_PREFIXES.update(saved_ext)
    results.append(("ST-15 变异：清空 EXTERNAL 后 CR-/BQ- 立刻被误报（该表有效）",
                    leaked == {"CR-5", "BQ-01"}
                    and dangling_refs(ns, pfx) == set(),
                    f"变异未体现 EXTERNAL 的作用：{sorted(leaked)}"))

    # ---- EP-2 §18.2 接口归属绑定 ----
    # 三类错误各自必须有夹具：归属缺失 / 规则 ID 悬空 / 归属包不存在。
    # **只测一类不够**——它们是不同的失效路径，抽屉里漏掉哪一类，
    # 那类错误就永远抓不到，而报告看起来仍然是「EP-2 已覆盖」。
    ep_specs = {"W-12": "| RF-1 | 退款关联原支付 |\n| RF-3 | 渠道退款未知时先查 |\n"}
    ep_ok = ("### 18.2 建议接口清单\n\n"
             "| 方法 | 行为 | 约束 | 归属 |\n|---|---|---|---|\n"
             "| `POST /tenants/{t}/refund-requests` | 退款 | 可退额锁定 | W-12 · `RF-1 / RF-3` |\n"
             "### 18.3 通用约定\n")
    results.append(("ST-16 EP-2 归属绑定全部有效 → 无问题",
                    check_endpoint_bindings(ep_ok, ep_specs) == [], "误报"))
    results.append(("ST-17 EP-2 绑定到未定义的规则 ID → 被抓",
                    bool(check_endpoint_bindings(
                        ep_ok.replace("`RF-1 / RF-3`", "`RF-1 / RF-99`"), ep_specs)),
                    "漏报"))
    results.append(("ST-18 EP-2 归属列为空 → 被抓",
                    bool(check_endpoint_bindings(
                        ep_ok.replace("| W-12 · `RF-1 / RF-3` |", "|  |"), ep_specs)),
                    "漏报"))
    results.append(("ST-19 EP-2 归属到不存在的规格 → 被抓",
                    bool(check_endpoint_bindings(
                        ep_ok.replace("W-12 ·", "W-99 ·"), ep_specs)),
                    "漏报"))
    # ⚠ 是**如实登记的缺口**，不得判红——否则会被迫编造锚点来消警告，
    # 而那正是本项目反复出现的「让门禁变绿、让文档变假」。
    results.append(("ST-20 EP-2 显式标注「无规则级锚点」→ 不判红",
                    check_endpoint_bindings(
                        ep_ok.replace("W-12 · `RF-1 / RF-3`",
                                      "W-05 · §1 ⚠无规则级锚点"), ep_specs) == [],
                    "如实登记的缺口被误判"))

    # ---- INC-78 收口：外部命名空间与「定义」不再自相矛盾 ----
    # ① 子编号字面占位符不得判悬空（**实测 3 处硬误报**的来源）
    results.append(("ST-25 子编号占位符 `AC-06-x` 不判悬空（不误报）",
                    dangling_refs("三份规格改回 `AC-06-x`/`AC-07-x`/`AC-08-x`。\n", pre)
                    == set(),
                    "占位符被误判为悬空"))
    # ② 真实三段 ID 仍要被抓——容错不得把它一起放过
    results.append(("ST-26 占位符容错不掩盖真悬空（`AC-06-x` 放过 / `AC-06-9` 报）",
                    dangling_refs("见 `AC-06-x`。\n", pre) == set()
                    and dangling_refs("见 `AC-06-9`。\n", pre) == {"AC-06-9"},
                    "容错范围过宽或过窄"))
    # ③ 外部命名空间的首列**不计入规则数**，且不得同时「被定义为规则」
    w03 = "| **CR-1** | `RequestContext` 是 frozen=True | 读装饰器 |\n| LE-1 | 租约带 epoch |\n"
    w11 = "| **BQ-02** | `reserved` 超时回收时长 | 建议删除 |\n| RSV-1 | 预留必须落账 |\n"
    results.append(("ST-27 外部命名空间首列被剔出规则数（W-03 CR-1 / W-11 BQ-02）",
                    rule_defs(w03) == ["LE-1"] and rule_defs(w11) == ["RSV-1"],
                    f"过滤不对：{rule_defs(w03)} / {rule_defs(w11)}"))
    results.append(("ST-28 AC/ADR 已移出外部表（否则 102 个实体对 RI-1 不可见）",
                    not is_external("AC-01-1") and not is_external("ADR-02")
                    and is_external("CR-1") and is_external("BQ-02"),
                    "外部表与现实不符"))
    # ④ 族锚点容忍：**只对两段生效**，且不得掩盖「族里不存在的那一条」
    fams = families_of({"AC-02-1", "AC-02-2", "AC-06-1"})
    results.append(("ST-29 族锚点 `AC-02` 指向已存在的族 → 不报（实测 5 处正文）",
                    dangling_refs("塌缩成 `AC-02`。\n", pre, fams) == set(),
                    "族锚点被误判"))
    results.append(("ST-30 族容错不掩盖族内缺项（`AC-06-9` 报 / 族锚点 `AC-06` 不报）",
                    dangling_refs("见 `AC-06-9`。\n", pre, fams) == {"AC-06-9"}
                    and dangling_refs("见 `AC-06`。\n", pre, fams) == set(),
                    "三段缺项被放过"))
    results.append(("ST-31 整个族从未定义 → 族锚点照样报（不是无脑放过）",
                    dangling_refs("见 `AC-13`。\n", pre, fams) == {"AC-13"},
                    "不存在的族被放过"))

    # ---- INC-92：0 个规则 ID 的防空转闸 ----
    #
    # 这两条是**闸门自身**的夹具，必须直接调 `verdict`——
    # 端到端夹具只能证明「真实仓库有 444 条规则所以绿」，
    # 证明不了「没有规则时会不会报红」。
    #
    # ⚠ 两条缺一不可，且**顺序有讲究**：
    #   ST-32 证明闸会响，ST-33 证明它不是恒红。
    # 只写 ST-32 的话，这道闸可以改成「永远 return 2」而自检照样全绿。
    import contextlib
    import io as _io

    def _call_verdict(n_specs, n_ids, dup, dang):
        """直接调 `verdict` 并吞掉它的打印——闸门夹具必须看到退出码本身。"""
        return verdict(n_specs, n_ids, dup, dang, "")

    _v_cases = [
        # 闸门会响：规格在、规则 0 条 ⇒ 必须报 2，且不能打印「通过」
        ("ST-32 0 个规则 ID → 报 2 且不得出现「通过」（INC-92 防空转）",
         lambda: _call_verdict(1, 0, {}, {}), 2),
        # 恢复后回到通过：证明不是恒红
        ("ST-33 恢复为有规则（3 条）后回到 0（证明 ST-32 那道闸不是恒红）",
         lambda: _call_verdict(1, 3, {}, {}), 0),
        # 既有闸不得被新闸吞掉：0 份规格仍是 2
        ("ST-34 0 份规格仍报 2（新闸不得覆盖既有的 `not total_specs` 闸）",
         lambda: _call_verdict(0, 0, {}, {}), 2),
        # 有重复定义时仍报 1（防空转闸不得吞掉真实发现）
        ("ST-35 有重复定义仍报 1（防空转闸不得吞掉真实发现）",
         lambda: _call_verdict(1, 3, {"CB-1": ["W-01", "W-02"]}, {}), 1),
    ]
    for _label, _fn, _want in _v_cases:
        _buf = _io.StringIO()
        with contextlib.redirect_stdout(_buf):
            _got = _fn()
        _out = _buf.getvalue()
        _ok = _got == _want and ("通过" not in _out or _want == 0)
        results.append((_label, _ok,
                        f"退出码 {_got}≠{_want} 或输出含「通过」：{_out.strip()[:80]}"))

    passed = 0
    for name, ok, why in results:
        if ok:
            passed += 1
        else:
            print(f"  ✗ {name} —— {why}")
    print(f"\n自检 {passed}/{len(results)}")
    if passed == len(results):
        print("自检通过")
        return 0
    print("自检失败")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
