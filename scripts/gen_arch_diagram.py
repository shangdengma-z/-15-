"""生成作品介绍/技术设计文档里那张运行架构图（PNG + SVG，同一份图两个用途）。

为什么不用 ASCII 图：Word 里等宽字体不成立，`├──` 这种对齐全会歪成一团（这是实际踩到的问题）。
GitHub 上仍保留 ASCII 版（md2docx 转 Word 时会自动把那段换成这张图，Markdown 源文件不改）。

    python scripts/gen_arch_diagram.py            # 产出 docs/assets/architecture.png / .svg

依赖只装开发环境：pip install matplotlib

排版思路：画布 100x100 坐标，盒子高度按"标题区 + 正文行数"算出，下一行的上边界 = 上一行下边界 -
ROW_GAP，所以加减文案不会溢出边框，箭头也不会压字。box() 还会按字号估一次每行宽度，放不下就直接
报错——图糊了没人看得见，报错才有。
"""

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

FIG_W, FIG_H = 12.4, 9.0
UX = FIG_W * 72 / 100                  # 一个 x 单位等于多少磅
UY = FIG_H * 72 / 100

INK = '#22302B'
ACCENT = '#4E807A'
SOFT = '#E7F0EE'
PAPER = '#FBFCFA'
LINE = '#C3CDBE'
DIM = '#6E7C73'
BLUE = '#8CAEC4'
SAGE = '#89AE87'
SAND = '#C9A257'

TITLE_PT, SUB_PT, BOX_PT, BODY_PT, LAB_PT = 13.5, 8.8, 9.8, 8.4, 7.6
LEAD = BODY_PT * 1.6 / UY              # 一行正文占多少 y 单位
HEAD = 5.6                             # 标题行 + 上留白
PAD_BOTTOM = 2.2
ROW_GAP = 3.6
TOP0 = 90.5

LX0, LX1 = 2.0, 46.0                   # 第 3/4 行的左格
RX0, RX1 = 54.0, 98.0                  # 第 3/4 行的右格
FX0, FX1 = 2.0, 98.0                   # 通栏
CX_L, CX_R = 24.0, 76.0                # 竖向箭头所在的列


def box_h(lines):
    return HEAD + len(lines) * LEAD + PAD_BOTTOM


def text_pt(s, size):
    """粗估字符串宽度：汉字/全角按一个字号，ASCII 按 0.56 个。"""
    return sum(size * (1.0 if ord(c) > 0x2E80 else 0.56) for c in s)


def box(ax, x0, x1, y_top, title, lines, fill='white', edge=LINE, tag=''):
    h = box_h(lines)
    inner = (x1 - x0) * UX - 4.8
    over = [t for t in (title, tag, *lines) if text_pt(t, BOX_PT) > inner]
    if over:
        raise ValueError(f'盒子 x{x0}-{x1} 放不下：{over[0]}')
    ax.add_patch(FancyBboxPatch((x0, y_top - h), x1 - x0, h,
                               boxstyle='round,pad=0,rounding_size=1.4',
                               linewidth=1.3, edgecolor=edge, facecolor=fill, zorder=3))
    ax.text(x0 + 2.4, y_top - 2.2, title, fontsize=BOX_PT, fontweight='bold',
            color=INK, va='top', ha='left', zorder=4)
    if tag:
        ax.text(x1 - 2.4, y_top - 2.7, tag, fontsize=LAB_PT, color=edge,
                va='top', ha='right', zorder=4, style='italic')
    ax.text(x0 + 2.4, y_top - HEAD, '\n'.join(lines), fontsize=BODY_PT, color=DIM,
            va='top', ha='left', linespacing=1.6, zorder=4)
    return y_top - h


def down_arrow(ax, x, y_from, y_to, label=''):
    """竖箭头。标签一律放在箭头右侧而不是压在线上——压线时要把线遮掉，箭头就断成截了。"""
    ax.annotate('', xy=(x, y_to + 0.5), xytext=(x, y_from - 0.5),
                arrowprops=dict(arrowstyle='-|>', color=ACCENT, lw=1.2), zorder=2)
    if label:
        ax.text(x + 1.0, (y_from + y_to) / 2, label, fontsize=LAB_PT, color=DIM,
                ha='left', va='center', zorder=4)


def side_arrow(ax, x_from, x_to, y, label='', color=ACCENT):
    ax.annotate('', xy=(x_to - 0.5, y), xytext=(x_from + 0.5, y),
                arrowprops=dict(arrowstyle='-|>', color=color, lw=1.2), zorder=2)
    if label:
        gap = abs(x_to - x_from)
        if text_pt(label, LAB_PT) > gap * UX - 4:
            raise ValueError(f'横向缝放不下标签：{label}')
        ax.text((x_from + x_to) / 2, y + 1.35, label, fontsize=LAB_PT, color=DIM,
                ha='center', va='center', zorder=4)


def main():
    plt.rcParams['font.family'] = 'Microsoft YaHei'
    fig = plt.figure(figsize=(FIG_W, FIG_H))
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis('off')

    ax.text(FX0, 98.6, '运行架构 · 出网全部收口在 BmapClient，一次采样多处复用',
            fontsize=TITLE_PT, fontweight='bold', color=INK, va='top')
    ax.text(FX0, 94.0, '单镜像同时提供 /api 与前端静态站；浏览器端地图 AK 由 /env.js 运行时注入，服务端 REST 的 AK 绝不进镜像',
            fontsize=SUB_PT, color=DIM, va='top')

    # 1 前端
    y = box(ax, FX0, FX1, TOP0, '前端 · Vue 3 + BMapGL + ECharts',
            ['四列工作台栅格（地图 / 参数 / 结果 / 侧栏）· 图层坞 · 详情弹窗 · 图例',
             '体检报告 · 导则达标对照 · 缺口片区 · 选址建议 · AI 助手 · 历史档案 · 地名搜索（下钻到县与县级市）',
             '密钥默认只存本浏览器 localStorage，随请求头带上，服务端不留副本'],
            fill=SOFT, edge=ACCENT, tag='浏览器端')

    top = y - ROW_GAP
    down_arrow(ax, 50, y, top, 'HTTP /api · Cookie + CSRF · 请求头可带密钥')

    # 2 应用层
    y = box(ax, FX0, FX1, top, 'FastAPI 应用层 · 异步作业与门禁',
            ['POST /api/analysis → job_id → 轮询；asyncio.to_thread + JOB_SLOTS 全局并发闸',
             'auth_gate：登录 + CSRF 双提交 + 每人每日额度；档案按 user_id 隔离，读别人的 id 一律 404',
             '计量响应头 X-Api-Calls / X-Quota-Units / X-Cache-Hits，调用量当场可核验'],
            edge=ACCENT, tag='单镜像')

    top = y - ROW_GAP
    down_arrow(ax, CX_L, y, top, '体检编排')
    down_arrow(ax, CX_R, y, top, '问答规划')

    # 3 业务层 | 智能体
    lb = box(ax, LX0, LX1, top, '算法与业务层',
             ['等时圈 18 射线 × 4 半径 → 5/10/15/20 分钟',
              'POI 四类设施 + 菜场/药店/小学，多词并查去重',
              '盲区 网格初筛 + 批量矩阵复核 + 连片聚合',
              '导则达标 · 选址三因子 · 情景重算 = 零出网'],
             edge=SAGE, tag='analysis')
    rb = box(ax, RX0, RX1, top, 'AgentLoop 规划智能体',
             ['OpenAI 兼容 /chat/completions + function calling',
              '6 个工具由模型自主编排，步数上限防跑飞',
              '工具报错回填给模型自纠，全过程留痕可回放',
              '未配 LLM_API_KEY 时降级规则模式，面板明标'],
             edge=SAND, tag='agent')
    side_arrow(ax, RX0, LX1, (top + rb) / 2, '工具调用', color=SAGE)

    top = min(lb, rb) - ROW_GAP
    down_arrow(ax, CX_L, lb, top, '出网只在这一处')
    down_arrow(ax, CX_R, rb, top, '落库')

    # 4 出网收口 | 存储与账号
    lb = box(ax, LX0, LX1, top, 'BmapClient 出网收口',
             ['分服务令牌桶：步行 30 / 检索 30 / 编码 10 QPS',
              'SN 签名 · 指数退避重试 · status=10 日配额熔断',
              '请求级磁盘缓存 data/cache，按用户分目录',
              'BMAP_MOCK=1 走模拟路网，零配额跑通全链路'],
             edge=BLUE, tag='baidu_client')
    rb = box(ax, RX0, RX1, top, '存储 · 账号层（挂载卷 ./data）',
             ['档案 SQLite：runs / coverage / blindspots',
              'agent_runs / traces；派生结果入库，原文不留',
              '账号四表 users / sessions / quotas / rate_log',
              'argon2id 口令 · SHA-256 会话 · Fernet 存 key'],
             edge=LINE, tag='db / auth')
    side_arrow(ax, LX1, RX0, (top + lb) / 2, '缓存落盘', color=DIM)

    top = min(lb, rb) - ROW_GAP
    down_arrow(ax, CX_L, lb, top, 'REST（服务端 AK+SK）')

    # 5 外部
    y = box(ax, FX0, FX1, top, '外部服务',
            ['百度地图开放平台：步行路径规划 v2 · 批量距离矩阵 · 圆形/城市内检索 · 正/逆地理编码 · JSAPI WebGL',
             '大模型 API：DeepSeek / 千帆等 OpenAI 兼容端点，换厂商只改 LLM_BASE_URL 与 LLM_MODEL'],
            fill=SOFT, edge=ACCENT, tag='外部')
    if y < 0.5:
        raise ValueError(f'画布高度不够，末行下沿到了 {y:.1f}')

    out = Path('docs/assets')
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / 'architecture.png', dpi=200, facecolor=fig.get_facecolor())
    fig.savefig(out / 'architecture.svg', facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f'已生成 docs/assets/architecture.png 与 .svg（末行下沿 y={y:.1f}，画布留白 {y:.1f}）')


if __name__ == '__main__':
    main()
