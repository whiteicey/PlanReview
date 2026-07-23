# 已知限制

- 冻结包不包含Provider凭据、本地数据库、上传文档或历史审查结果；首次部署必须在本机安全配置Provider。
- 最新成功Run的`llm_status`为`COMPLETED_PARTIAL`，但5个批次均以`end_turn`完成，无`validation_reason_code`或`llm_error_summary`，最终结果已完整持久化。
- 六条V1.2新增规则在本次真实验证环境中全部启用；规则能力仍受文档对象、范围、单位和上下文可归一程度限制，证据不足时返回`UNKNOWN`。
- 生命周期账本用于诊断，受可配置容量上限保护；本次成功Run两份账本均未截断。
- Provider瞬时网络故障可能触发重试；成功重试不会视为最终失败。
- 当前前端为核心版本既有界面。横版UI尚未集成，必须在独立`feature/v1.2-horizontal-ui`工作区完成，不能覆盖本冻结仓库。
- 本冻结包不包含DEFECT20 manifest、缺陷答案或离线评测产物，这些材料不得进入生产审查链路。
