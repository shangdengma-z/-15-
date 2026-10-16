"""把 Markdown 作品介绍文档转成可提交的 .docx（中文投稿排版：封面 + 目录域 + 页码 + 宋体正文首行缩进 + 三线表）。

为什么自己写转换而不是手动粘进 Word：文档的权威版本必须与仓库里的 Markdown 同源，
否则改了 README/技术文档，介绍文档还是旧数字。这条命令随时可重跑：

    python scripts/md2docx.py docs/作品介绍文档.md docs/作品介绍文档.docx

想调排版只改下面的常量：BODY_SIZE 正文字号、INDENT_CHARS 首行缩进（2 字符是中文习惯）、
LINE_SPACING 行距、COVER=True/False 是否要封面与目录。
Markdown 里那张 ASCII 架构图（无语言标注的代码块、含 ─│ 字符）会自动换成 docs/assets/architecture.png，
因为 Word 没有全局等宽保证，`├──` 对齐会歪成一团；图由 scripts/gen_arch_diagram.py 生成。

依赖只装开发环境（跑服务不需要，所以不进 requirements.txt）：pip install python-docx matplotlib
"""

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
ARCH_IMG = ROOT / 'docs' / 'assets' / 'architecture.png'
ARCH_CHARS = '─│┌┐└┘├┤┬┴┼'

INLINE = re.compile(r'(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|<?https?://[^\s)》，、<>]+>?)')

# —— 排版参数（改这里就够了）——
COVER = True
TITLE = '15 分钟生活圈 · 智能体检与规划助手'
SUBTITLE = '2026 第二届上海开源软件应用创新大赛 · 开源 AI 工具赛道 · 百度地图企业命题 · '
BODY_SIZE = Pt(12)          # 小四
LINE_SPACING = 1.5
INDENT_CHARS = 200          # 首行缩进 2 字符（用 firstLineChars 才能随字号缩放）
CODE_SHADE = 'F2F5F1'
COVER_SPACER = 13           # 封面信息表前的空段数：把表格压到下半页
IMG_WIDTH = Cm(15.4)        # 通栏图宽 = A4 减左右边距
INK = RGBColor(0x22, 0x30, 0x2B)
ACCENT = RGBColor(0x4E, 0x80, 0x7A)
CODE_RGB = RGBColor(0x33, 0x50, 0x4B)
LINK_RGB = RGBColor(0x2F, 0x55, 0x6F)
CAPTION_RGB = RGBColor(0x6E, 0x7C, 0x73)

BOLD_RE = re.compile(r'^\*\*(.+?)\*\*$')


# ---------- 底层小工具 ----------

def east_asia(style_or_run, name):
    """只设 ascii 字体不设 eastAsia，中文会掉回默认字体——中文文档的必踩坑。"""
    rpr = style_or_run.element.get_or_add_rPr() if hasattr(style_or_run, 'element') else style_or_run._element.get_or_add_rPr()
    fonts = rpr.find(qn('w:rFonts'))
    if fonts is None:
        fonts = OxmlElement('w:rFonts')
        rpr.append(fonts)
    fonts.set(qn('w:eastAsia'), name)


def field(par, instr_text, placeholder=''):
    """插入一个 Word 域（PAGE 页码 / TOC 目录），打开后按 F9 或"更新域"刷新。"""
    run = par.add_run()
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = instr_text
    separate = OxmlElement('w:fldChar')
    separate.set(qn('w:fldCharType'), 'separate')
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    for el in (begin, instr, separate):
        run._r.append(el)
    if placeholder:
        hint = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = placeholder
        hint.append(text)
        run._r.append(hint)
    run._r.append(end)


def shade(par, hexval):
    pPr = par._p.get_or_add_pPr()
    el = OxmlElement('w:shd')
    el.set(qn('w:val'), 'clear')
    el.set(qn('w:fill'), hexval)
    pPr.append(el)


def first_line(par, chars=INDENT_CHARS):
    pPr = par._p.get_or_add_pPr()
    ind = pPr.find(qn('w:ind'))
    if ind is None:
        ind = OxmlElement('w:ind')
        pPr.append(ind)
    ind.set(qn('w:firstLineChars'), str(chars))


def table_rules(table):
    """三线表：只有顶线、表头下线、底线，无竖线——比 Word 默认的满框表格更适合报告。"""
    tblPr = table._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge, val, sz in (('top', 'single', 12), ('bottom', 'single', 12), ('insideH', 'single', 6),
                          ('left', 'none', 0), ('right', 'none', 0), ('insideV', 'none', 0)):
        e = OxmlElement(f'w:{edge}')
        e.set(qn('w:val'), val)
        if val != 'none':
            e.set(qn('w:sz'), str(sz))
            e.set(qn('w:color'), '4E807A' if edge in ('top', 'bottom') else 'BBC6BD')
        borders.append(e)
    tblPr.append(borders)


# ---------- 文档样式 ----------

def setup_styles(doc):
    n = doc.styles['Normal']
    n.font.name = 'Times New Roman'
    n.font.size = BODY_SIZE
    east_asia(n, '宋体')
    n.paragraph_format.line_spacing = LINE_SPACING
    n.paragraph_format.space_after = Pt(0)
    n.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    for i, (size, name) in enumerate(((16, 'Heading 1'), (13.5, 'Heading 2'), (12, 'Heading 3')), 1):
        st = doc.styles[name]
        st.font.name = 'Microsoft YaHei'
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = INK if i > 1 else ACCENT
        east_asia(st, '微软雅黑')
        st.paragraph_format.space_before = Pt(14 if i == 1 else 10)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.line_spacing = 1.3
    q = doc.styles['Intense Quote']
    q.font.size = Pt(11)
    q.font.color.rgb = RGBColor(0x44, 0x51, 0x4C)
    east_asia(q, '楷体')
    for nm in ('List Bullet', 'List Number'):
        doc.styles[nm].font.size = BODY_SIZE
        east_asia(doc.styles[nm], '宋体')


def setup_page(doc):
    s = doc.sections[0]
    s.page_height, s.page_width = Cm(29.7), Cm(21.0)
    s.top_margin, s.bottom_margin = Cm(2.8), Cm(2.6)
    s.left_margin, s.right_margin = Cm(2.8), Cm(2.8)
    hp = s.header.paragraphs[0]
    hp.text = ''
    r = hp.add_run(TITLE)
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(0x93, 0xA0, 0x99)
    east_asia(r, '宋体')
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    fp = s.footer.paragraphs[0]
    fp.text = ''
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    a = fp.add_run('第 ')
    a.font.size = Pt(9)
    field(fp, 'PAGE', '1')
    b = fp.add_run(' 页 / 共 ')
    b.font.size = Pt(9)
    field(fp, 'NUMPAGES', '1')
    c = fp.add_run(' 页')
    c.font.size = Pt(9)
    for run in fp.runs:
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x93, 0xA0, 0x99)


def cover(doc, kind='作品介绍文档'):
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(TITLE)
    r.font.size = Pt(26)
    r.font.bold = True
    r.font.color.rgb = ACCENT
    east_asia(r, '微软雅黑')
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run('基 于 地 图 开 放 能 力 的 社 区 生 活 圈 体 检 与 规 划 助 手')
    r2.font.size = Pt(12)
    r2.font.color.rgb = INK
    east_asia(r2, '楷体')
    doc.add_paragraph()
    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r3 = p3.add_run(SUBTITLE + kind)
    r3.font.size = Pt(10.5)
    r3.font.color.rgb = RGBColor(0x6E, 0x7C, 0x73)
    east_asia(r3, '宋体')
    # 信息表压到下半页：spacer 用固定磅值的空段，不随字号漂移
    for _ in range(COVER_SPACER):
        sp = doc.add_paragraph()
        sp.paragraph_format.space_after = Pt(0)
        sp.paragraph_format.line_spacing = 1.0
        sp.add_run().font.size = Pt(15)
    meta = doc.add_table(rows=4, cols=2)
    meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    rows = (('在线演示', 'https://zhaoboyi.online'),
            ('代码仓库', 'https://github.com/shangdengma-z/15fen（公开仓库 · MIT 许可证）'),
            ('配套文档', 'README.md · 技术设计文档.docx · ROADMAP.md'),
            ('参赛主体', '学生团队（本科）'))
    for i, (k, v) in enumerate(rows):
        for j, txt in enumerate((k, v)):
            cell = meta.cell(i, j)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            cell.width = Cm(2.8) if j == 0 else Cm(12.2)
            par = cell.paragraphs[0]
            par.alignment = WD_ALIGN_PARAGRAPH.RIGHT if j == 0 else WD_ALIGN_PARAGRAPH.LEFT
            par.paragraph_format.space_before = Pt(4)
            par.paragraph_format.space_after = Pt(4)
            par.paragraph_format.line_spacing = 1.25
            run = par.add_run(txt)
            run.font.size = Pt(10.5)
            run.bold = j == 0
            run.font.color.rgb = INK if j == 0 else RGBColor(0x44, 0x51, 0x4C)
            east_asia(run, '宋体')
            if j == 0:
                east_asia(run, '微软雅黑')
    doc.add_page_break()
    t = doc.add_paragraph()
    r = t.add_run('目 录')
    r.font.size = Pt(14)
    r.bold = True
    east_asia(r, '微软雅黑')
    toc = doc.add_paragraph()
    field(toc, r'TOC \o "1-2" \h \z \u', '（在 Word 里右键此处 → 更新域，即可生成目录）')
    doc.add_page_break()


# ---------- Markdown 解析 ----------

def add_runs(par, text, bold=False):
    """行内语法：粗体、行内码、链接、裸 URL。粗体里可套行内码，所以递归回本函数。"""
    for tok in INLINE.split(text):
        if not tok:
            continue
        if tok.startswith('**') and tok.endswith('**'):
            add_runs(par, tok[2:-2], bold=True)
        elif tok.startswith('`') and tok.endswith('`'):
            r = par.add_run(tok[1:-1])
            r.font.name = 'Consolas'
            r.font.size = Pt(10)
            r.font.color.rgb = CODE_RGB
            r.bold = bold
        elif tok.startswith('['):
            label, url = re.match(r'\[([^\]]+)\]\(([^)]+)\)', tok).groups()
            r = par.add_run(label)
            r.font.color.rgb = LINK_RGB
            r.underline = True
            r.bold = bold
            if url.startswith('http'):
                par.add_run(f'（{url}）').font.size = Pt(9)
        elif tok.lstrip('<').startswith('http'):
            r = par.add_run(tok.strip('<>'))
            r.font.color.rgb = LINK_RGB
            r.underline = True
            r.bold = bold
        else:
            par.add_run(tok).bold = bold


def add_table(doc, rows):
    cells = [[c.strip() for c in r.strip().strip('|').split('|')] for r in rows]
    cells = [r for r in cells if not re.match(r'^[\s|:-]+$', '|'.join(r))]
    if not cells:
        return
    ncol = max(len(r) for r in cells)
    t = doc.add_table(rows=len(cells), cols=ncol)
    table_rules(t)
    for i, row in enumerate(cells):
        for j in range(ncol):
            par = t.cell(i, j).paragraphs[0]
            par.paragraph_format.line_spacing = 1.15
            par.paragraph_format.space_after = Pt(2)
            par.alignment = WD_ALIGN_PARAGRAPH.LEFT
            add_runs(par, row[j] if j < len(row) else '')
            for r in par.runs:
                r.font.size = Pt(10)
                r.bold = r.bold or i == 0
                east_asia(r, '宋体')
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_code(doc, lines):
    for cl in lines:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Pt(16)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.1
        shade(p, CODE_SHADE)
        r = p.add_run(cl or ' ')
        r.font.name = 'Consolas'
        r.font.size = Pt(9.5)
        r.font.color.rgb = CODE_RGB
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_arch_image(doc):
    """ASCII 架构图在 Word 里必然歪（等宽不成立），换成 scripts/gen_arch_diagram.py 生成的图。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(ARCH_IMG), width=IMG_WIDTH)
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(8)
    r = cap.add_run('图 1 运行架构（源图 docs/assets/architecture.svg，由 scripts/gen_arch_diagram.py 生成）')
    r.font.size = Pt(9)
    r.font.color.rgb = CAPTION_RGB
    east_asia(r, '楷体')


def convert(src: Path, dst: Path):
    doc = Document()
    setup_styles(doc)
    setup_page(doc)
    if COVER:
        cover(doc, '技术设计文档' if '技术设计' in src.stem else '作品介绍文档')
    lines = src.read_text(encoding='utf-8').splitlines()
    i, in_code, lang, code, table = 0, False, '', [], []
    while i < len(lines):
        line = lines[i]
        if line.startswith('```'):
            if in_code:
                ascii_art = (not lang) and any(ch in ARCH_CHARS for cl in code for ch in cl)
                if ascii_art and ARCH_IMG.is_file():
                    add_arch_image(doc)
                else:
                    add_code(doc, code)
                code, in_code, lang = [], False, ''
            else:
                in_code, lang = True, line[3:].strip()
            i += 1
            continue
        if in_code:
            code.append(line)
            i += 1
            continue
        if line.strip().startswith('|'):
            table.append(line)
            i += 1
            continue
        if table:
            add_table(doc, table)
            table = []
        s = line.strip()
        if not s or (set(s) == {'-'} and len(s) < 4):
            i += 1
            continue
        if s.startswith('# '):
            p = doc.add_heading(level=1)
            add_runs(p, s[2:])
        elif s.startswith('## '):
            p = doc.add_heading(level=2)
            add_runs(p, s[3:])
        elif s.startswith('### '):
            p = doc.add_heading(level=3)
            add_runs(p, s[4:])
        elif s.startswith('> '):
            add_runs(doc.add_paragraph(style='Intense Quote'), s[2:])
        elif re.match(r'^[-*] ', s):
            add_runs(doc.add_paragraph(style='List Bullet'), s[2:])
        elif re.match(r'^\d+[.)] ', s):
            add_runs(doc.add_paragraph(style='List Number'), re.sub(r'^\d+[.)] ', '', s))
        else:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(4)
            first_line(p)
            add_runs(p, s)
        i += 1
    if table:
        add_table(doc, table)
    doc.save(dst)
    return dst


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit('用法: python scripts/md2docx.py <in.md> <out.docx>')
    out = convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f'已生成 {out}（{out.stat().st_size // 1024} KB）')
