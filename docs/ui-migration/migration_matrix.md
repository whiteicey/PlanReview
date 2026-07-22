# V1.2 横版UI选择性迁移矩阵

## 基线

- V1.2核心：`616eb8186f989922d28df2592e219f8688152ca8`
- 目标分支：`feature/v1.2-horizontal-ui`
- 旧UI来源：`C:\Users\连浚杰\Documents\AI培训\PlanReview`
- 旧UI提交：`68e05de8713b8440c906e0b1fdeea829123abc89`
- 旧UI工作树只读使用，未执行修改、stash或reset。

## 差异分类

| 范围 | 决策 | 说明 |
|---|---|---|
| 暖白底色、橙红强调色、左中右工作台构图 | PORT_AS_IS | 视觉语言按用户提供参考图复现 |
| `index.html`横版区域组织 | PORT_ADAPTED | 改为单一业务DOM；不复制Run、Finding或进度组件 |
| `styles.css`桌面/简化/紧凑布局 | PORT_ADAPTED | 使用CSS Grid和同一DOM响应式重排 |
| 旧`layout.js` | PORT_ADAPTED | 删除DOM搬移逻辑，仅设置布局CSS状态和本地偏好 |
| 旧`workbench_state.js` | DROP_OLD | 原文件存在乱码且错误解释AI计数，按V1.2 Schema重写纯adapter |
| 旧`app.js`业务重写 | DROP_OLD | 保留V1.2上传、Run创建、Finding复核、导出和恢复业务链路 |
| V1.2 `review_state.js` | KEEP_V1.2 | SHA-256保持不变 |
| V1.2 `review_display_queue.js` | KEEP_V1.2 | SHA-256保持不变 |
| `review_progress.js` | PORT_ADAPTED | 仅增加只读状态回调和中文状态标签，不建立第二轮询器 |
| 旧仓库所有后端、规则、Provider和Prompt改动 | DROP_OLD | 未迁移 |

## 单一状态约束

- 页面中`upload`、`review-progress-root`和`result`均只有一个实例。
- 只有一次`createReviewProgressController`初始化。
- 三种布局不移动或重挂载业务DOM，仅改变`body`布局数据属性和CSS。
- `workbench_state.js`只派生显示值，不保存第二份业务状态。
