r"""CI 用的零密钥全链路断言：真起一个 uvicorn，打一次体检，逐项核对返回的报告结构。

为什么不把这段断言直接写在 .github/workflows/ci.yml 里：GitHub 的 `run: |` 是 YAML 块标量，
里面的多行 `python -c '` 一旦有顶格的行就会**结束块标量**，整个 workflow 变成"Invalid workflow
file"、一次都没跑过（这是本项目真实踩到过的坑，见 README 的 CI 一节）。断言放进 .py 文件后
CI 那一行只是 `python scripts/ci_full_chain.py <url>`，YAML 里再无引号嵌套。

    python scripts/ci_full_chain.py http://localhost:8001      # 服务已在跑
    python scripts/ci_full_chain.py --self-start               # 自己起服务（本地复现用）

只依赖标准库：CI 机器上不额外装 requests。
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def http(url, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def assert_report(r: dict) -> str:
    """报告里每一项都对应一个"如果代码改坏了，这里必须红"的结论。"""
    assert len(r['rings']['15']) >= 12, '等时圈顶点不足'
    assert r['coverage'] and all(v['total'] >= 0 for v in r['coverage'].values()), '覆盖统计缺失'
    assert 'missing' in json.dumps(r['blindspots'], ensure_ascii=False) or r['blindspots'] == [], '盲区字段异常'
    cl = r['blindspot_clusters']
    assert sum(c['cells'] for c in cl) == len(r['blindspots']), '片区聚合丢格/重计'
    assert all(c['cells'] >= 1 and c['area_km2'] > 0 and 'missing' in c for c in cl), '片区字段不完整'
    assert len(r['meta']['evidence_id']) == 16, '证据指纹缺失'
    sc = r['scenarios']['by_crowd']
    assert sc['elderly']['area_km2'] < sc['adult']['area_km2'], '人群步速换算方向错误'
    assert r['scenarios']['api_calls_for_scenarios'] == 0, '情景重算不应出网'
    assert r['metrics'] and all('formula' in m for m in r['metrics']), '可解释指标缺公式'
    std = r['standards']
    assert std['total'] >= 5 and std['method'], '导则达标对照缺失'
    assert all(i['ref_m'] > 0 and i['status'] in ('达标', '临界', '缺口', '无点位') for i in std['items']), '导则状态取值异常'
    assert std['passed'] == sum(1 for i in std['items'] if i['status'] == '达标'), '达标计数不自洽'
    adv = r.get('advice') or {}
    assert adv.get('candidates') is None or all('score' in c for c in adv['candidates']), '选址候选缺评分'
    return (f'健康分 {r["health_score"]} | 盲区 {len(r["blindspots"])} 处'
            f' | 导则达标 {std["passed"]}/{std["total"]} 项 | 等时圈顶点 {len(r["rings"]["15"])}'
            f' | 热力点 {len(r["heat"])} | 老人/成人面积 {sc["elderly"]["area_km2"]}/{sc["adult"]["area_km2"]}')


def run(base: str, lat: float, lng: float, budget_s: int) -> int:
    t0 = time.time()
    health = http(f'{base}/api/health')
    assert health.get('ok'), f'健康检查失败：{health}'
    assert health.get('mock'), '这条断言要求 BMAP_MOCK=1，不该出网'
    job = http(f'{base}/api/analysis', {'lat': lat, 'lng': lng})
    job_id = job['job_id']
    while time.time() - t0 < budget_s:
        time.sleep(1.5)
        d = http(f'{base}/api/analysis/{job_id}')
        if d['status'] == 'done':
            print(f'零密钥全链路 OK（{time.time() - t0:.1f}s）：{assert_report(d["result"])}')
            return 0
        if d['status'] == 'error':
            print(f'FAIL  任务报错：{d}')
            return 1
    print(f'FAIL  {budget_s}s 内没跑完')
    return 1


def main() -> int:
    args = [a for a in sys.argv[1:]]
    lat, lng, budget = 40.0703, 116.3306, 60
    if '--self-start' in args:
        env = dict(os.environ, BMAP_MOCK='1', BMAP_QPS='1000', AUTH_ENABLED='0',
                   BMAP_DB_PATH=os.path.join(ROOT, 'data', 'db_ci_fullchain', 'lifestyle.db'))
        port = '8012'
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
                                 '--port', port, '--app-dir', os.path.join(ROOT, 'backend')],
                                env=env, cwd=ROOT)
        try:
            time.sleep(4)
            return run(f'http://127.0.0.1:{port}', lat, lng, budget)
        finally:
            proc.terminate()
    if not args:
        print(__doc__)
        return 2
    return run(args[0].rstrip('/'), lat, lng, budget)


if __name__ == '__main__':
    sys.exit(main())
