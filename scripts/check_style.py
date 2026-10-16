r"""仓库格式守门：一条命令查完"提交前该自己修掉"的那几类脏东西。

为什么不用 ruff format / prettier：这两者是"按我的口味重排整个文件"，跑一次会产生几千行 diff，
评审看不出质量、却把 blame 全洗掉了。本脚本只查**不会引起争议的那部分约定**——
行尾空白、文件末尾换行、tab 缩进、同一文件里混用 CRLF/LF、BOM 位置、Python 行宽——
它们要么是真 bug 诱因（CRLF 混进 shell 脚本会让 `docker run` 里的 `\\` 续行失效），
要么是 diff 噪音来源，且修起来零风险。

    python scripts/check_style.py            # 有违规则 exit 1，CI 直接红
    python scripts/check_style.py --fix      # 就地修（只修安全的那几类）

只查 `git ls-files` 里的文件：未跟踪的临时导出（聊天记录、下载的 PDF）不该由它背锅。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

BINARY_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.pdf', '.docx', '.xlsx',
              '.pyc', '.ico', '.woff', '.woff2', '.zip'}
# PowerShell 5.1 读不到无 BOM 的 UTF-8 中文脚本，所以 .ps1 的 BOM 是必需的，不是脏东西
BOM_REQUIRED = {'.ps1'}
PY_MAX = 120
INDENT_TAB = re.compile(r'^\t+|^\s*\t')


def tracked_files(root: Path):
    out = subprocess.run(['git', 'ls-files'], cwd=root, capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip():
        return [f.replace('\\', '/') for f in out.stdout.split()]
    # 没有 git（比如从 zip 解压出来评审）时退回全量扫描
    return [str(p.relative_to(root)).replace('\\', '/')
            for p in root.rglob('*')
            if p.is_file() and not {'.git', '.venv', 'node_modules', 'dist', 'data', '__pycache__'} & set(p.parts)]


def check_one(text: str, name: str, fixable: bool):
    """返回 (违规列表, 修正后的文本)。行尾 \\r 单独算一类违规，其余按 \\n 切行处理。"""
    errs = []
    lines = text.split('\n')
    crlf = sum(1 for ln in lines if ln.endswith('\r'))
    if crlf and crlf < len(lines) - 1:
        # 半 CRLF 半 LF：git 会按 .gitattributes 二次归一，本地却天天显示"整个文件改过"
        errs.append('换行符混用（CRLF 与 LF 并存）')
    for i, ln in enumerate(lines, 1):
        bare = ln.rstrip('\r')
        if bare != bare.rstrip():
            errs.append(f'行尾空白 :{i}')
            if fixable:
                lines[i - 1] = bare.rstrip()
        if name.endswith('.py') and INDENT_TAB.match(bare):
            errs.append(f'tab 缩进 :{i}')
        if name.endswith('.py') and len(bare) > PY_MAX:
            errs.append(f'行宽 {len(bare)} > {PY_MAX} :{i}')
    if fixable:
        lines = [ln.rstrip('\r') for ln in lines]
    if text and not text.endswith('\n'):
        errs.append('文件末尾缺换行')
        if fixable:
            lines.append('')
    return errs, '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fix', action='store_true', help='就地修可修的那几类（行尾空白/末尾换行/统一 LF）')
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    bad, fixed = 0, 0
    for rel in tracked_files(root):
        p = root / rel
        if not p.is_file() or p.suffix.lower() in BINARY_EXT:
            continue
        raw = p.read_bytes()
        bom = raw.startswith(b'\xef\xbb\xbf')
        errs = []
        if bom and p.suffix.lower() not in BOM_REQUIRED:
            errs.append('多余的 UTF-8 BOM')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            errs.append('不是 UTF-8 文本')
            print(f'FAIL  {rel}: {errs[0]}')
            bad += 1
            continue
        if bom:
            text = text[1:]
        e, new_text = check_one(text, rel, args.fix)
        errs += e
        if errs:
            print(f'FAIL  {rel}: ' + '; '.join(errs[:4]) + (f' 等 {len(errs)} 处' if len(errs) > 4 else ''))
            bad += len(errs)
            if args.fix:
                keep_bom = bom and p.suffix.lower() in BOM_REQUIRED
                p.write_bytes((('\ufeff' if keep_bom else '') + new_text).encode('utf-8'))
                fixed += 1
    if args.fix and fixed:
        print(f'已修 {fixed} 个文件，请复查 diff')
        return 0
    print(f'== 格式检查：{"发现 %d 处违规" % bad if bad else "全部通过"} ==')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
