# Wiki Knowledge Base（中文说明）

> **知识会复利。** 一个让 AI Agent 主动维护、持续改进、积累理解的结构化知识系统——不只是检索。

完整文档请参阅 [README.md](README.md)。以下是核心设计理念的中文概述。

---

## 我们在解决什么问题

每个 AI 应用都会撞上同一堵墙：**知识不积累。**

RAG 每次检索同样的文档。对话历史被压缩后遗忘。Agent 每次回答同一个问题给出不同的答案。没有任何东西在复利。

Karpathy 在 2026 年 4 月点出了这个问题：*"LLM 每次查询都从零推导知识，没有积累。"* 他的 LLM Wiki 提出了一个转向——**在写入时编译知识，而不是在查询时检索**——GitHub Gist 一周内获得 14,000 star。

Garry Tan 围绕同样的洞察构建了 [gbrain](https://github.com/garrytan/gbrain)：每个实体一个 Markdown 文件，上半部分是 **compiled truth**（当前最佳理解），下半部分是 **append-only timeline**（证据链）。当理解和证据冲突时，证据胜出。结构战胜幻觉。

我们从这些想法出发。然后花了三个月在生产环境中运行一个真实的知识库——创建了 58 个页面，构建了自动化脚本，迭代了多个 schema 版本——学到了什么真正有效，什么只是听起来不错。

**这个项目是我们学到的东西。**

---

## 核心设计决策

### 从生产中诞生，不是从理论中设计

我们构建了自动化审计（Dream Cycle）、记忆同步（memory_to_wiki）、知识图谱（auto_index）、7 个 cron 任务、9 种页面类型。然后在实际运行中发现大部分没有产生价值：

| 我们构建的 | 实际结果 | 决定 |
|-----------|---------|------|
| Dream Cycle（LLM 审计） | Cron 要么没跑，要么产出表面反馈 | **砍掉**。定向 wiki_review > 自动审计 |
| memory_to_wiki 同步 | OpenViking 搜索 API 不索引 memories，脚本空转 | **砍掉**。先修上游 |
| auto_index（知识图谱） | graph.json 生成了但没有消费者 | **砍掉**。YAGNI |
| 9 种页面类型 | Agent 分类混乱，tool vs entity vs project 难以区分 | **3 种**。concept, entity, person |
| 7 个 cron 任务 | 运行数周只有 2 条 STARTED 记录 | **不要 cron**。Agent 按需触发 |

> **Schema 就是产品。** 类型越少，Agent 分类越准。脚本越少，维护越少。Cron 越少，死代码越少。

### Compiled Truth + Timeline — 结构战胜 Prompt

每个页面两层：上面是可重写的当前理解，下面是只追加的证据链。当两者矛盾时，证据胜。这不是 prompt 技巧，而是结构约束——让 AI 生成的知识可审计、可自纠。

### 优雅降级

生产环境教会我们依赖总会失败。每个组件都有降级方案：OpenViking 挂了 → 本地文件搜索；MCP session 过期 → 24 小时超时；搜索返回垃圾 → 自动过滤 + 降级。

**Docker + Markdown = 全部可用。OpenViking = 搜索更聪明。就这样。**

### MCP 原生 — 知识即工具

Wiki KB 是标准 MCP Server，15 个工具。Agent 不是"查询知识库"——而是像使用 web_search、file_edit 一样**使用知识工具**。换模型零成本。

---

## 致谢

- **[Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)** — 原始洞察：写入时编译，查询时定位
- **[Garry Tan's gbrain](https://github.com/garrytan/gbrain)** — Compiled Truth + Timeline 模式，生产级验证
- **[LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)** — 记忆生命周期、知识图谱、混合搜索扩展
- **[元Skill方法论](https://mp.weixin.qq.com/s/AZ_DFAFf-J7V6MUcH77iLA)** — "好东西 = 总结出来 ≠ 设计出来"

---

## License

MIT
