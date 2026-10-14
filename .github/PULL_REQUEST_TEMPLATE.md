## 动机与影响面

<!-- 为什么改？解决什么问题？影响哪些模块（后端 / 前端 / 算法 / 配置 / CI）？ -->

## 实现要点

<!-- 关键设计选择，尤其是"为什么不用另一种做法"。算法改动请写清复杂度与调用量变化。 -->

## 配额影响

<!-- 是否新增百度 API 调用？单次完整体检的调用数从多少变到多少？个人 AK 步行仅 3 QPS / 5000 次每天。 -->

## 测试

- [ ] `ruff check backend/app --select E,F,W --ignore E501` 通过
- [ ] `python backend/tests/test_agent_offline.py` 全部 PASS
- [ ] `cd frontend && npm run build` 通过
- [ ] Mock 模式端到端跑通（`bash scripts/demo.sh`，能看到体检报告）
- [ ] 改了采样/盲区参数？说明对已预热缓存的失效影响

## 截图 / 录屏

<!-- 界面或地图渲染变更必须附图；纯内部重构可写"无界面变更"。 -->
