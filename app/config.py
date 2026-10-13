import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    server_ak: str = field(default_factory=lambda: _env("BMAP_SERVER_AK"))
    server_sk: str = field(default_factory=lambda: _env("BMAP_SERVER_SK"))
    qps: float = field(default_factory=lambda: float(_env("BMAP_QPS", "3")))
    concurrency: int = field(default_factory=lambda: int(_env("BMAP_CONCURRENCY", "4")))
    cache_dir: str = field(default_factory=lambda: _env("BMAP_CACHE_DIR", "data/cache"))
    mock: bool = field(default_factory=lambda: _env("BMAP_MOCK", "0") == "1")
    rays: int = field(default_factory=lambda: int(_env("ISOCHRONE_RAYS", "24")))
    sample_radii: str = field(default_factory=lambda: _env("ISOCHRONE_RADII", "300,600,900,1200,1500,1800"))
    walk_target_sec: int = field(default_factory=lambda: int(_env("ISOCHRONE_TARGET_SEC", "900")))
    city: str = field(default_factory=lambda: _env("SAMPLE_CITY", "北京市"))


settings = Settings()

# 民生设施类别 -> 百度 POI 检索关键词（tag 语义清洗在 poi.py 里做二次过滤）
CATEGORIES = {
    "medical": {"name": "基础医疗", "queries": ["药店", "社区卫生服务中心", "诊所"], "keywords_any": ["药店", "药房", "诊所", "卫生服务", "卫生室", "门诊"]},
    "education": {"name": "教育", "queries": ["小学", "幼儿园"], "keywords_any": ["小学", "幼儿园"]},
    "shopping": {"name": "购物", "queries": ["菜市场", "超市", "便利店"], "keywords_any": ["菜市场", "菜场", "农贸", "超市", "便利"]},
    "elderly": {"name": "养老", "queries": ["养老院", "社区养老", "日间照料"], "keywords_any": ["养老", "照料中心", "老年"]},
}

# 赛题要求的强制核验类别：菜市场 / 药店 / 小学
REQUIRED_CATEGORIES = {"shopping_market": ("菜市场", 1000), "pharmacy": ("药店", 1000), "primary_school": ("小学", 1000)}
