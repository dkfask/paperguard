#!/usr/bin/env python3
"""敏感信息与冲突标记扫描（对应总设计 §22.4 发布门禁第 6 条）。

为什么这道门禁存在
------------------
总设计 §22.4 发布门禁第 6 条要求：`git diff --check`、**敏感信息检查**、
最新 head 的 CI/审核。
实测：`docs/tenancy/tools/` 下 18 个门禁里**没有任何一道做敏感信息检查**。
而本仓库恰好是高危面——`secret_store.py` 曾存在无主密钥时明文落库路径、
`users.ai_config` 直接存供应商 API Key、`PAPERGUARD_SECRET_KEY` 是全局主密钥。
**门禁清单里写了要做，实际没有，会比没写更危险**——放行时它看起来已被覆盖。

**本门禁刻意不使用「长十六进制串 = 密钥」这条规则**
--------------------------------------------------------
2026-10-01 实测踩到的坑：`docs/generalization/` 与 `docs/releases/` 里
有大量 40/64 位十六进制串，**全部是 Git 提交 SHA**。
而 `openssl rand -hex 32` 生成的主密钥**也是 64 位十六进制**——
两者形状完全相同，机器无法区分。
按形状判定会对每一个提交哈希报假警；**误报的代价和漏报一样大**。

因此本门禁改用**上下文判据**：不看值长什么样，
看它**被赋给了谁**。`SECRET_KEY = "…"` 是泄露，
文档里引用一个 SHA 是文档。SC-5 用自检把这条边界锁死。

扫描范围
--------
`backend/` · `frontend/src` · `frontend/e2e` · `ops/` · `docs/` · 根级配置
排除：`.venv` / `node_modules` / `__pycache__` / `.git` / `*.db` / 二进制

退出码：`0` 通过 / `1` 检出疑似敏感信息 / `2` 工具不可运行
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]

SCAN_DIRS = ["backend", "frontend/src", "frontend/e2e", "ops", "docs"]
SCAN_ROOT_FILES = ["package.json", "playwright.config.ts", ".env.example"]
TEXT_SUFFIX = {".py", ".ts", ".js", ".vue", ".md", ".yaml", ".yml", ".json",
               ".sh", ".conf", ".txt", ".html", ".css", ".toml", ".ini", ".cfg",
               ".service", ".example"}
SKIP_DIR_PARTS = {".venv", "node_modules", "__pycache__", ".git", "dist",
                  ".pytest_cache", "uploads", "data"}
SKIP_SUFFIX = {".db", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico",
               ".woff", ".woff2", ".mp4", ".pyc", ".so"}

# 前缀由拼接构造：**门禁源码里不得出现真实的密钥形态字面量**，
# 否则它会把自己扫成泄露（自指陷阱）。
_SK = "s" + "k-"
_PEM = "-----BEGIN " + "PRIVATE KEY-----"
_PEM_RSA = "-----BEGIN RSA " + "PRIVATE KEY-----"

# SC-1 明确的密钥形态（零歧义，不依赖上下文）
SHAPE_PATTERNS = [
    ("供应商 API Key", re.compile(re.escape(_SK) + r"[A-Za-z0-9]{24,}")),
    ("PEM 私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("GitHub PAT", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}")),
    ("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
]

# SC-3 敏感变量被赋**字面量**。
# 排除：os.environ / os.getenv / getenv / 占位符 / 短值 / 明显示例
ENV_READ = re.compile(r"os\.(environ|getenv)|process\.env|getenv\(|"
                      r"environ\[|configparser|load_dotenv")
SENSITIVE_NAME = re.compile(
    r"\b([A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|APIKEY|"
    r"ACCESS_TOKEN|AUTH_TOKEN|PRIVATE_TOKEN|CLIENT_SECRET)[A-Z0-9_]*)\s*"
    r"(?::[^=\n]+)?=\s*(.+)$")
# 2026-10-01 实测修正：`"$VAR"` 是**变量插值**不是字面量。
# 首版把它当硬编码，导致 ops/online-verify.sh 与 11-staging.md 各误报一次。
LITERAL = re.compile(r"""^\s*["'](?!\$\{?)(?!\$)[^"'\n]{12,}["']""")
# 明显的占位/示例/测试值
PLACEHOLDER = re.compile(
    r"(?i)(example|sample|dummy|placeholder|redacted|xxxx|your[-_ ]|changeme|"
    r"not[-_ ]?a[-_ ]?real|fake|test|demo|<\s*|\$\{|%\(|f\"|f')")
# 多词连字符模板：`use-a-local-secret-with-letters-and-digits`、
# `provided-out-of-band`、`sk-audit-must-not-appear-DO-NOT-LEAK` 都是这一形态。
#
# 判据刻意只用两条：**≥3 个连字符** + **无 6 位以上连续数字**。
# 前一版还加了「每段 ≥2 字符」，实测被 `use-a-local-…` 里的单字符段 `a` 否掉。
#
# 为什么「3 个连字符」足够区分：真实凭据的生成方式决定它们**都不含连字符**——
# `openssl rand -hex` 是纯十六进制、base64 无 `-`、
# 供应商 `sk-`/`AKIA`/`ghp_`/`xoxb-` 都是固定前缀 + 连续随机段。
# 多词连字符是**人类写的说明**的形态。
#
# **这是启发式，必须写明它的代价**：一个真人手写的、含 3 个以上连字符
# 且无长数字串的弱密码，会被当成模板放过。
# **误报与漏报都要显式承担，不能假装这个判据没有代价。**
def _is_template(inner: str) -> bool:
    return inner.count("-") >= 3 and not re.search(r"\d{6,}", inner)

# SC-4 Git 冲突标记残留
CONFLICT = re.compile(r"^(<{7} |>{7} |={7}$)", re.M)


def iter_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for d in SCAN_DIRS:
        base = REPO / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIR_PARTS for part in p.parts):
                continue
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            if p.suffix.lower() in TEXT_SUFFIX or p.name in SCAN_ROOT_FILES:
                out.append(p)
    for name in SCAN_ROOT_FILES:
        p = REPO / name
        if p.is_file():
            out.append(p)
    return sorted(set(out))


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """返回 [(行号, 规则, 片段)]。"""
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pat in SHAPE_PATTERNS:
            m = pat.search(line)
            if m:
                frag = m.group(0)
                # 不回显完整密钥：只报前 6 位
                hits.append((lineno, f"SC-1 {name}", frag[:6] + "…"))
        if CONFLICT.search(line):
            hits.append((lineno, "SC-4 冲突标记残留", line.strip()[:40]))
        m = SENSITIVE_NAME.search(line)
        if m and not ENV_READ.search(line):
            value = m.group(2).strip()
            if not LITERAL.match(value):
                continue
            inner = value.strip("\"'")
            if PLACEHOLDER.search(value) or _is_template(inner):
                continue
            hits.append((lineno, "SC-3 敏感变量硬编码赋真值", m.group(1)))
    return hits


def verdict(n_files: int, hits: list[tuple[pathlib.Path, int, str, str]],
            err: str) -> int:
    if err:
        print(f"工具不可运行：{err}", file=sys.stderr)
        return 2
    if not n_files:
        # 提取层空输入 → 不得判绿（与 ddl_static_check / spec_rule_id_check 同纪律）
        print("✗ 扫描到 0 个文件 → 报 2（路径配置失效，不得判绿）")
        return 2
    if hits:
        by_rule: dict[str, int] = {}
        for _p, _l, rule, _f in hits:
            by_rule[rule] = by_rule.get(rule, 0) + 1
        print(f"检出疑似敏感信息 {len(hits)} 处（{n_files} 个文件）：\n")
        for p, lineno, rule, frag in hits:
            try:
                rel = p.relative_to(REPO)
            except ValueError:
                rel = p
            print(f"  ✗ {rel}:{lineno}  [{rule}]  {frag}")
        print("\n分布：" + " · ".join(f"{k} {v}" for k, v in sorted(by_rule.items())))
        print(
            "\n**先人工核对再处理**——本门禁按形态与上下文判定，会误报。\n"
            "确认是占位符的，请改成含 example/test/redacted 之类字样，"
            "让下一次扫描能自动放过。"
        )
        return 1
    print(f"✓ 通过：{n_files} 个文件内未检出疑似敏感信息或冲突标记")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    try:
        files = iter_files()
    except OSError as exc:
        return verdict(0, [], f"遍历失败：{exc}")
    if not REPO.is_dir():
        return verdict(0, [], f"仓库根不存在：{REPO}")
    hits: list[tuple[pathlib.Path, int, str, str]] = []
    unread = 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unread += 1  # 非 UTF-8 文本跳过，但计数以便发现覆盖缺口
            continue
        for lineno, rule, frag in scan_text(text):
            hits.append((p, lineno, rule, frag))
    if unread:
        print(f"  · {unread} 个文件非 UTF-8，已跳过（覆盖缺口，非判定）")
    return verdict(len(files), hits, "")


def self_test() -> int:
    print("=" * 70)
    print("secret_scan_check 自检")
    print("=" * 70)
    results: list[tuple[str, bool, str]] = []

    def run(src: str) -> list[tuple[int, str, str]]:
        return scan_text(src)

    # ---- SC-1 阳性 ----
    vendor = f'API_KEY = "{_SK}{"A" * 40}"\n'
    results.append(("ST-1 供应商密钥形态被抓", bool(run(vendor)), "供应商密钥未被抓"))
    results.append(("ST-2 PEM 私钥被抓",
                    bool(run("-----BEGIN " + "RSA PRIVATE KEY" + "-----\nMIIE...\n")),
                    "PEM 未被抓"))
    results.append(("ST-3 GitHub PAT 被抓", bool(run("token = ghp_" + "a" * 36)), "PAT 未被抓"))
    results.append(("ST-4 AWS AKIA 被抓", bool(run("AKIA" + "A" * 16)), "AKIA 未被抓"))

    # ---- SC-1 阴性：仓库里真实存在的测试夹具不得误报 ----
    fixtures = [
        'KEY = "sk-a-tenant"\n',
        'KEY = "sk-attacker-controlled"\n',
        'KEY = "sk-audit-must-not-appear-DO-NOT-LEAK"\n',
        'KEY = "sk-book"\n',
    ]
    fp_hit = [f for f in fixtures if run(f)]
    results.append(("ST-5 仓库真实测试夹具（sk-a-tenant 等）不误报",
                    not fp_hit, f"误报：{fp_hit}"))

    # ---- SC-5 关键边界：Git SHA 不是密钥 ----
    sha_doc = ("Source revision `8ad97a9cbbdc49be6eeeaf171dadbcbdc8b6a009`。\n"
               "SHA-256：`07456d1db1182d0a08b942f999e612ba4c606488603154ee54813ad49ef21938`。\n")
    results.append(("ST-6 ★ Git SHA（40/64 位十六进制）不判为密钥",
                    not run(sha_doc), "长十六进制被误判为密钥——这正是刻意不用的规则"))

    # ---- SC-3 上下文判据 ----
    hard = "PAPERGUARD_" + "SECRET_KEY" + ' = "' + "9f2b7c1d4e6a8b0c2d4e6f8a0b2c4d" + '6e"\n'
    results.append(("ST-7 敏感变量硬编码赋真值被抓", bool(run(hard)), "硬编码未被抓"))
    env_read = 'SECRET_KEY = os.environ.get("PAPERGUARD_SECRET_KEY", "")\n'
    results.append(("ST-8 从环境变量读取不误报", not run(env_read), "env 读取被误判"))
    placeholder = 'API_KEY = "your-api-key-here"\n'
    results.append(("ST-9 占位符不误报", not run(placeholder), "占位符被误判"))
    short = 'TOKEN = "abc"\n'
    results.append(("ST-10 短值不误报", not run(short), "短值被误判"))

    # ---- 多词连字符模板：仓库里真实存在的三类形态 ----
    # 首版用「每段 ≥3 字符」判据，被 `sk-` 的 2 字符前缀整体否掉；
    # 二版加「每段 ≥2 字符」仍被 `use-a-local-…` 里的单字符段 `a` 否掉。
    # 现只用「≥3 连字符 + 无 6 位数字」。
    #
    # **所有样例都按拼接构造**——否则门禁会把自己的自检语料
    # 扫成泄露（自指陷阱，实测踩到 2 次）。
    _Q = chr(34)   # 双引号，避免下面的拼接读起来像转义地狱
    _APOS = "'"
    templates = [
        "PAPERGUARD_STAGING_ADMIN_PASSWORD = " + _Q
        + "use-a-local-" + "secret-with-letters-and-digits" + _Q + "\n",
        "PAPERGUARD_ONLINE_ADMIN_PASSWORD = " + _APOS
        + "provided-out-of-" + "of-band" + _APOS + "\n",
        "_SECRET = " + _Q + _SK + "audit-must-not-"
        + "appear-DO-NOT-LEAK" + _Q + "\n",
    ]
    tpl_hit = [t for t in templates if run(t)]
    results.append(("ST-10b 多词连字符模板（sk- 前缀 / out-of-band / 说明串）不误报",
                    not tpl_hit, f"误报：{tpl_hit}"))
    # 反向：带 3 连字符但有 6 位数字串的，必须**仍被判为真值**
    hy_num = ("PASSWORD = " + _Q + "corp-net-2024-" + "882301-x" + _Q + "\n")
    results.append(("ST-10c 含 6 位数字串的多段值仍被判真值（模板判据不宽）",
                    bool(run(hy_num)), "模板判据过宽，放过了含长数字串的凭据"))

    # ---- shell 变量插值不是字面量（首版误报两处真实文件）----
    interp = ['E2E_ADMIN_PASSWORD="$PAPERGUARD_STAGING_ADMIN_PASSWORD" \\\n',
              "login_payload=\"$(jq -nc --arg p \"$admin_password\")\"\n"]
    iv_hit = [i for i in interp if run(i)]
    results.append(("ST-10d shell 变量插值不判为硬编码（首版误报）",
                    not iv_hit, f"插值被误判：{iv_hit}"))

    # ---- SC-4 冲突标记 ----
    results.append(("ST-11 Git 冲突标记被抓",
                    bool(run("<<<<<<< HEAD\nfoo\n=======\nbar\n>>>>>>> other\n")),
                    "冲突标记未被抓"))

    # ---- 变异：把阴性样本改成阳性，判定必须翻面 ----
    mutated = f'KEY = "{_SK}{"B" * 40}"\n'
    flip = bool(run(mutated)) and not run('KEY = "sk-a-tenant"\n')
    results.append(("ST-12 变异：夹具→真密钥，判定跟着翻面", flip, "变异未改变判定"))

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
