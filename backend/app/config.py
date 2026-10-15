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
    # 限流按服务独立计。2026-10-14 账号的路线规划配额升到 30 QPS / 1 万次每天，
    # 之前记录的是个人 AK 的默认档（步行 3 QPS / 5000 次），见 baidu_client.py 与 .env 注释
    qps: float = field(default_factory=lambda: float(_env("BMAP_QPS", "30")))
    concurrency: int = field(default_factory=lambda: int(_env("BMAP_CONCURRENCY", "10")))
    place_qps: float = field(default_factory=lambda: float(_env("BMAP_PLACE_QPS", "25")))
    place_concurrency: int = field(default_factory=lambda: int(_env("BMAP_PLACE_CONCURRENCY", "8")))
    geo_qps: float = field(default_factory=lambda: float(_env("BMAP_GEO_QPS", "8")))
    geo_concurrency: int = field(default_factory=lambda: int(_env("BMAP_GEO_CONCURRENCY", "4")))
    cache_dir: str = field(default_factory=lambda: _env("BMAP_CACHE_DIR", "data/cache"))
    db_path: str = field(default_factory=lambda: _env("BMAP_DB_PATH", "data/db/lifestyle.db"))
    mock: bool = field(default_factory=lambda: _env("BMAP_MOCK", "0") == "1")
    rays: int = field(default_factory=lambda: int(_env("ISOCHRONE_RAYS", "18")))
    sample_radii: str = field(default_factory=lambda: _env("ISOCHRONE_RADII", "300,600,1000,1500"))
    walk_target_sec: int = field(default_factory=lambda: int(_env("ISOCHRONE_TARGET_SEC", "900")))
    city: str = field(default_factory=lambda: _env("SAMPLE_CITY", "北京市"))
    max_candidates: int = field(default_factory=lambda: int(_env("BLINDSPOT_MAX_CANDIDATES", "16")))
    verify_nearest: int = field(default_factory=lambda: int(_env("BLINDSPOT_VERIFY_NEAREST", "3")))

    # 规划智能体：OpenAI 兼容接口，默认对接 DeepSeek，换厂商只需改 BASE_URL/MODEL
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.deepseek.com"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "deepseek-chat"))
    agent_max_steps: int = field(default_factory=lambda: int(_env("AGENT_MAX_STEPS", "4")))


settings = Settings()

# 民生设施类别 -> 百度 POI 检索关键词（tag 语义清洗在 poi.py 里做二次过滤）
CATEGORIES = {
    "medical": {"name": "基础医疗", "queries": ["药店", "社区卫生服务中心", "诊所"], "keywords_any": ["药店", "药房", "诊所", "卫生服务", "卫生室", "门诊"]},
    "education": {"name": "教育", "queries": ["小学", "幼儿园"], "keywords_any": ["小学", "幼儿园"]},
    "shopping": {"name": "购物", "queries": ["菜市场", "超市", "便利店"], "keywords_any": ["菜市场", "菜场", "农贸", "超市", "便利"]},
    "elderly": {"name": "养老", "queries": ["养老院", "社区养老", "日间照料"], "keywords_any": ["养老", "照料中心", "老年"]},
}

# 赛题要求的强制核验类别：从大类检索结果中按名称关键词切出单品，不额外消耗配额
REQUIRED_CATEGORIES = {
    "shopping_market": {"name": "菜市场", "limit": 1000, "source": "shopping",
                        "keywords": ["菜市场", "菜场", "农贸"]},
    "pharmacy": {"name": "药店", "limit": 1000, "source": "medical",
                 "keywords": ["药店", "药房"]},
    "primary_school": {"name": "小学", "limit": 1000, "source": "education",
                       "keywords": ["小学"]},
}

# 典型社区预设：演示时一键切换不同城市形态，覆盖赛题要求的"城乡结合部/老旧小区"对照
PRESET_COMMUNITIES = [
    {"id": "huilongguan", "name": "回龙观居住区", "archetype": "城乡结合部大型居住区",
     "lat": 40.0703, "lng": 116.3306},
    {"id": "jiadaokou", "name": "交道口老城街区", "archetype": "中心城区老旧小区",
     "lat": 39.9403, "lng": 116.4038},
    {"id": "tiantongyuan", "name": "天通苑大区", "archetype": "超大型保障房社区",
     "lat": 40.0748, "lng": 116.4180},
    {"id": "wangjing", "name": "望京西区", "archetype": "城市新区综合社区",
     "lat": 40.0010, "lng": 116.4700},
    {"id": "guomao", "name": "国贸中心区", "archetype": "高密度商务中心",
     "lat": 39.9087, "lng": 116.4560},
]
