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
    # 限流按服务独立计（百度个人 AK 实测：步行 3 QPS、地点检索 30 QPS），见 baidu_client.py
    qps: float = field(default_factory=lambda: float(_env("BMAP_QPS", "3")))
    concurrency: int = field(default_factory=lambda: int(_env("BMAP_CONCURRENCY", "3")))
    place_qps: float = field(default_factory=lambda: float(_env("BMAP_PLACE_QPS", "25")))
    place_concurrency: int = field(default_factory=lambda: int(_env("BMAP_PLACE_CONCURRENCY", "8")))
    geo_qps: float = field(default_factory=lambda: float(_env("BMAP_GEO_QPS", "8")))
    geo_concurrency: int = field(default_factory=lambda: int(_env("BMAP_GEO_CONCURRENCY", "4")))
    cache_dir: str = field(default_factory=lambda: _env("BMAP_CACHE_DIR", "data/cache"))
    mock: bool = field(default_factory=lambda: _env("BMAP_MOCK", "0") == "1")
    rays: int = field(default_factory=lambda: int(_env("ISOCHRONE_RAYS", "18")))
    sample_radii: str = field(default_factory=lambda: _env("ISOCHRONE_RADII", "300,600,1000,1500"))
    walk_target_sec: int = field(default_factory=lambda: int(_env("ISOCHRONE_TARGET_SEC", "900")))
    city: str = field(default_factory=lambda: _env("SAMPLE_CITY", "北京市"))
    max_candidates: int = field(default_factory=lambda: int(_env("BLINDSPOT_MAX_CANDIDATES", "8")))
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
