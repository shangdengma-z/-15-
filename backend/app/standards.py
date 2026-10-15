"""导则达标对照：把圈内设施逐项对齐公开规划参考半径（纯计算，零新增出网）。

取值口径写死在这里，报告里原样透出，避免"我们的分数凭什么这么算"无从核对。
注意：判定用的是**直线最近距离**（POI 已在手，不再烧步行配额），所以状态一律标注为
"直线初筛"；需要路网实测时看盲区清单，那里已过批量距离矩阵复核。
"""

from .baidu_client import straight_line_m
from .isochrone import point_in_polygon

# 参考半径：GB 50180-2018《城市居住区规划设计标准》五分钟/十分钟生活圈配套设施，
# 与上海《15 分钟社区生活圈规划导则》常用取值合并后的工程口径，属规划参考值而非本地强制标准。
GUIDE_ITEMS = [
    {"key": "convenience", "name": "便利店 / 超市", "ref_m": 300, "pool": "shopping",
     "keywords": ["便利", "超市"], "basis": "GB50180 五分钟生活圈"},
    {"key": "market", "name": "菜市场 / 生鲜", "ref_m": 500, "pool": "shopping",
     "keywords": ["菜市场", "菜场", "农贸"], "basis": "上海 15 分钟导则"},
    {"key": "pharmacy", "name": "药店", "ref_m": 500, "pool": "medical",
     "keywords": ["药店", "药房"], "basis": "上海 15 分钟导则"},
    {"key": "clinic", "name": "社区卫生服务", "ref_m": 1000, "pool": "medical",
     "keywords": ["卫生服务", "卫生室", "卫生站", "诊所", "门诊"], "basis": "GB50180 十分钟生活圈"},
    {"key": "kindergarten", "name": "幼儿园", "ref_m": 500, "pool": "education",
     "keywords": ["幼儿园"], "basis": "GB50180 五分钟生活圈"},
    {"key": "primary_school", "name": "小学", "ref_m": 1000, "pool": "education",
     "keywords": ["小学"], "basis": "GB50180 十分钟生活圈"},
    {"key": "elderly", "name": "养老 / 日间照料", "ref_m": 1000, "pool": "elderly",
     "keywords": ["养老", "照料", "老年"], "basis": "上海 15 分钟导则"},
]

NEAR_RATIO = 1.25   # 超过参考半径 25% 以内算"临界"，再多判"缺口"


def compliance_check(center: tuple, pois: dict, ring15: list) -> dict:
    """逐项目给出：圈内数量、中心点直线最近距离、与参考半径的差距与状态。"""
    items = []
    for spec in GUIDE_ITEMS:
        pool = [p for p in pois.get(spec["pool"], []) if any(k in p["name"] for k in spec["keywords"])]
        inside = [p for p in pool if point_in_polygon((p["lng"], p["lat"]), ring15)]
        nearest = min(pool, key=lambda p: straight_line_m(center, (p["lat"], p["lng"])), default=None)
        near_m = round(straight_line_m(center, (nearest["lat"], nearest["lng"]))) if nearest else None
        if near_m is None:
            status = "无点位"
        elif near_m <= spec["ref_m"]:
            status = "达标"
        elif near_m <= spec["ref_m"] * NEAR_RATIO:
            status = "临界"
        else:
            status = "缺口"
        items.append({"name": spec["name"], "ref_m": spec["ref_m"], "inside": len(inside),
                      "nearest_m": near_m, "nearest_name": nearest["name"] if nearest else None,
                      "status": status, "basis": spec["basis"]})
    passed = sum(1 for i in items if i["status"] == "达标")
    return {"items": items, "passed": passed, "total": len(items),
            "method": "直线最近距离初筛（不再消耗步行配额），路网实测见盲区清单"}
