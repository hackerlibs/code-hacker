# Harness 即一切：模型不是不够聪明，而是手脚不够多

> "从第一性来说，模型不是不够聪明，而是是否有足够有用的tools。"
> —— Steve.桥布施, AI 生产力训练营群聊, 2026-05-20

> "模型越来越强，harness 就可以越来越松。"
> —— GeniusVczh

本文借一段微信群里关于 Claude Code / Codex / 第三方 agent 的讨论，结合本项目 `code-hacker` 的实际代码，谈谈我对 **harness（脚手架）** 这件事的理解：为什么 agent 之间真正的差距，越来越不在模型本身，而在你给模型装上的那双手。

---

## 一、那段对话讲了什么

讨论的起点是 cc（Claude Code）源码"泄露"和 OpenAI Codex 之间的比较，结论一边倒地指向同一个事实：

- 在 cc 和 codex 这一档上，**orchestration / harness 已经基本拉平**。
- 真正决定上限的，是 **模型本身的推理与代码生成能力**。
- 而对于使用者来说，决定下限的，是 **你为模型准备了什么 tools**。

几条原话值得抄下来反复看：

1. **"真正的壁垒不在 cc。"** —— harness 本身的工程价值在收敛，第三方 agent（opencode、claudrust 等）相比 cc 的劣势主要是工程稳定性，而不是设计思路。
2. **"模型越强，harness 就可以越来越松。"** —— 以前我们要写一堆规则去逼模型跑测试、写 commit message、不乱删代码；现在只需要"提一嘴"。
3. **"模型不是不够聪明，而是是否有足够有用的 tools。"** —— 这是整段对话最核心的一句。LLM 是文本生成器，它能不能解决一个问题，取决于它能不能 **通过 tools 把世界的状态读进来、把行动作用回去**。
4. **"你主要做的东西决定了你要增强的 tools 是什么。"** —— 写爬虫、做逆向、改 Jenkinsfile、维护多仓库，cc 自带的 Read/Edit/Bash 都不够。
5. **"精准 tools 即精准上下文。"** —— 一个好的 tool 调用，能把模型本来需要绕 5 个 loop 才能拼出来的信息，**一次返回**。这等价于给模型一个更短、更准、更便宜的 prompt。

这套观点和 Anthropic 内部说的"模型强大之后 harness 限制可以少一些，让它自己发现 best practice"是同一件事的两面：

- 模型变强 → harness 的"约束"部分可以变松（不用再手把手教）。
- 模型变强 → harness 的"能力"部分必须变厚（要给它接触新世界的接口）。

约束在退潮，能力在涨潮。`code-hacker` 这个项目，本质就是**涨潮那一侧**的实践。

---

## 二、code-hacker 是怎么"加手脚"的

`code-hacker` 不是又一个 agent UI，它的核心定位是一个**专为编程任务设计的 tool 阵列**。三个前端（VS Code Custom Agent、`web_app.py`、`tui_app.py`）共享同一套后端 —— **6 个 MCP server，62+ 个 tool**。

```
filesystem.py    → MCP Server 1: 文件 CRUD / 精准 edit / ag 正则搜索 (8001)
git_tools.py     → MCP Server 2: Git 操作 (8002)
code_intel.py    → MCP Server 3: AST 分析、符号、依赖图 (8003)
memory_store.py  → MCP Server 4: 持久化经验记忆 (CozoDB)        (8004)
code_review.py   → MCP Server 5: 代码质量评分 / 结构化 ydiff    (8005)
multi_project.py → MCP Server 6: 多仓库工作区                  (8007)
```

这 6 个 server 不是平铺的"我也支持读文件"，每一个都解决一个 **cc 自带 tools 想做但做不深** 的问题。下面挨个对照群聊里那句"精准 tools 即精准上下文"看。

### 1. `code_intel.py` —— 把"读代码"从字符串提升到 AST

cc 自带的 Read + Grep，模型看到的是字符串。要理解"这个函数被谁调用、它依赖什么、它在依赖图的哪个位置"，模型必须一遍一遍 grep、读、推、再 grep。

`code_intel` 直接给出：

- `analyze_python_file` —— 一次返回类、函数、导入、docstring 的 AST 视图
- `extract_symbols` —— 跨语言（Py / JS / TS / Java / Go / Rust）的符号提取
- `find_references` —— 跨文件符号引用
- `dependency_graph` —— 文件 import / imported-by 关系

对模型来说，**5 个 grep loop 变成 1 个 tool call**。这就是"精准 tool 即精准上下文"的字面意义。

### 2. `code_review.py` 里的 `ydiff` —— 把 diff 从行级别提升到结构级别

普通 `git diff` 是行 diff。一个函数被移动 50 行，模型看到的是"+50 行 / -50 行"，没法判断这是搬家还是改逻辑。

`ydiff_files` / `ydiff_commit` / `ydiff_git_changes` 直接给出**结构化 diff**：哪些函数被移动、哪些被改了签名、哪些是纯逻辑变更。这恰好支撑了项目里那条 **"Two-Phase Commit"** 工作流：

> 1. 机械搬迁 commit → 打 `#not-need-review`
> 2. 逻辑变更 commit → 正常 commit
>
> reviewer 用 `git log --grep="#not-need-review" --invert-grep` 一键跳过搬家 commit。

这条工作流之所以可行，是因为 tool 能告诉模型"这个改动是不是 identity transformation"。**没有这个 tool，模型只能猜；有了这个 tool，模型能直接证明。**

### 3. `memory_store.py` —— 给模型装上"上一次怎么解决的"记忆

群聊里那句 "harness 越来越松" 有一个前提：**你得把上下文以某种结构沉淀下来**，不然每次都是冷启动。

`memory_store` 用 CozoDB 提供一组分类 finder：

```
find_email_template / find_jira_template / find_bugfix /
find_pipeline / find_devops_lib / find_ai_knowledge
```

这里有个细节值得品味：为什么不是一个万能的 `memory_search` 就完事？因为**分类 finder 是更精准的 tool**。模型要找一个"Airflow DAG retry storm"的修复套路，调用 `find_pipeline(query="airflow retry")` 比 `memory_search(query="airflow retry")` 命中率高得多，假阳性少得多 —— 又一次回到"精准 tool 即精准上下文"。

而 `memory_save(title, category, problem, context, solution, pattern, tags)` 这个字段切分本身也是 harness 在塑造模型行为：它逼模型在保存经验时把 **pattern（可复用策略）** 单独抽出来，而不是把整段对话原样塞进去。这是 harness 教模型"如何记忆"的方式。

### 4. `multi_project.py` —— cc 没有的那只手

cc 默认是单仓库视角的。但真实世界里，"改一下库 + 同步更新 Jenkinsfile + 改一下下游服务的调用" 是日常。

`workspace_search` / `workspace_find_dependencies` / `workspace_edit_file` / `workspace_commit` 把多仓库当成一个"虚拟 monorepo"。模型不再需要被人手动 `cd` 来 `cd` 去，它可以**自己规划跨仓库的影响面**。

这就是群聊里说的："你主要做的东西决定了你要增强的 tools 是什么。" 如果你的工作流是多仓库的，你就需要 multi-project tools；如果你做逆向，你就要 debugger / 内存分析 tools；如果你做爬虫，你就要 headless browser tools。**cc 不会替你写这些，因为 cc 不知道你在干什么。**

### 5. `filesystem.py` 里的 `edit_file` —— 别小看那条"精准替换"

`edit_file(old_string, new_string)` 看起来朴素，但它是整套 tool 链里最被高频调用的那一个。它的设计哲学和 cc 的 Edit 工具一致：

- 强制 old_string 必须唯一 → 模型必须先读，再改，不允许蒙
- 失败时返回有用的错误（多次匹配 / 找不到）→ 模型能 self-correct
- 不返回整个文件 → token 便宜

这个 tool 的形状本身就是一种 harness。**它鼓励"小步、精准、可验证"的修改节奏**，把"AI 一改改一大坨"那种典型失败模式从源头掐掉。

---

## 三、harness 设计的几条第一性原理

把上面这些放在一起，可以提炼出几条我认为做 agent harness 的人都应该认账的原理：

### 1. Tool = 把外部世界压成 token 的函数

模型本身只能读 token、生成 token。一个 tool 的价值，是把"读 5 个文件 + 跑 1 个命令 + 解析 JSON"这一连串外部操作，**压缩成一次 tool call 的输入/输出**。压缩比越高，模型的 loop 越短，错误率越低，账单越便宜。

### 2. 精准 tool > 通用 tool

`find_pipeline` 比 `memory_search` 好，因为它的类型签名本身就携带了"这是个 pipeline 问题"的先验。同理 `ydiff_commit` 比 `git_diff` 好，`analyze_python_file` 比 `read_file` 好。

**一个 tool 的名字和参数，就是它附赠给模型的 prompt。** 命名和签名设计本身就是 prompt engineering。

### 3. Tool 设计要服务于工作流，不要服务于"覆盖度"

不要做"我也支持读文件、我也支持写文件、我也支持执行命令"这种平铺式 tool 矩阵。要问：

- 我的用户主要在做什么？
- 这个工作流里，哪几步是模型反复绕弯的？
- 这几步能不能用一个 tool 一次性返回？

`code-hacker` 选了 6 个领域（FS、Git、AST、Memory、Review、Multi-Project），不是因为这 6 个最"基础"，而是因为这 6 个最高频。

### 4. 模型越强，越要相信它

群聊里那句"以前我也自己写了个简单的 [harness]，现在直接删了"是个很重要的信号。早期 agent 的 harness 充满"先读项目结构、然后再读相关文件、然后再问用户确认"这种**约束链**，是因为模型蠢、容易跑偏。

现在不一样了。Claude Opus / Sonnet 4.x、Codex 这一代，**你给它正确的 tools，它自己会想出比你写死的工作流更聪明的路径**。harness 的约束部分应该退场，只留下两件事：

- **能力扩展**（接口、tools、记忆）
- **安全边界**（不能 rm -rf、不能动密钥、不能跨权限）

中间那层"教模型怎么干活"的 SOP，越来越没必要写死。这也是 `code-hacker` 的 `code-hacker.agent.md` 越来越短的方向 —— Core Working Principles 那一节最终会缩成几行。

### 5. 文本是 LLM 友好的中间层

群聊里有句话：

> "tools 不够可以自己加，LLM text based 这一点还是挺方便。"

这个一点不显眼但很关键。LLM 时代加 tool 的成本，比传统软件加 API 的成本低一个数量级 —— 因为输入输出都是文本，schema 是 JSON。**没有合适的工具就自己写一个 MCP server**，这件事现在不是基础设施工程，而是一次午饭时间的工作。

这也是为什么本项目把每个领域都拆成独立 MCP server（端口 8001-8007），而不是塞进一个大 binary —— **加 tool 的边际成本要低到你愿意为单个任务定制一个**。

---

## 四、回到那个反例：逆向工程为什么 cc 不够

群聊里 @GeniusVczh 提到他给微软的 toolchain 加了 debugger 和内存泄漏分析 tools，并且吐槽"微软居然不做"。这是个完美的反例：

- 模型能力：足够 —— Claude 完全能理解 WinDbg 命令、能读 dump、能推理内存布局。
- 模型 tools：不够 —— cc 默认只能 `Read` / `Bash`，你让模型在终端里手敲 WinDbg 然后用 grep 解析输出？loop 长得吓人，每一步都可能错。
- 加上专门的 debugger MCP server 之后：模型一次 `debug_get_call_stack(pid=123)` 拿到结构化 stack，一次 `debug_find_leak(snapshot_id=...)` 拿到候选泄漏点。**问题从"模型解不出"变成"模型 5 步解出"**。

这个例子比抽象的论证更有说服力：**同一个模型，加上对的 tools，行为差异不是百分比级别的，是数量级级别的。**

---

## 五、对 `code-hacker` 使用者 / 贡献者的几条具体建议

1. **不要把 `code-hacker` 当 "另一个 Claude Code 替代品" 用。** 它的价值在于那 62 个 tool，尤其是 cc 没有的那批（ydiff、multi-project、memory_store 分类 finder）。要让模型真的用上这些 tool —— 例如，你提的问题里直接说"用 ydiff 看看这个 commit"。

2. **加 tool 之前先问：这个 tool 会替模型省掉几个 loop？** 如果答案是 0，这个 tool 多半不该加。每多一个 tool，模型选择 tool 的认知负担就多一点，要克制。

3. **加 tool 之后立刻更新 `code-hacker.agent.md` 和 `README.md`。** Tool 的名字和签名是它给模型的"自我介绍"，文档则是它给模型的"使用场景说明"。两者缺一不可。这就是 `AGENTS.md` 里那条 "When adding a new MCP tool, register it in the relevant server file, then update `code-hacker.agent.md`'s tool listing and `README.md`" 的真正意义。

4. **能用 `edit_file` 就别用 `write_file`。** 这不是性能问题，是 harness 在保护你 —— `edit_file` 强制模型先理解再修改。

5. **遇到模型卡住的时候，第一反应不是"换更强的模型"，而是"我是不是少了一个 tool"。** 群聊那段对话其实就是这句话的展开。

---

## 六、结语

那段微信群聊里其实藏着一个挺反直觉的结论：**在 cc 和 codex 这个档位上继续比 harness 是不划算的**（这俩已经收敛了）；接下来真正能拉开差距的，是**你愿不愿意为自己的领域做专属 tools**。

`code-hacker` 不试图做下一个 cc，它做的是 cc 之上 / 之外 / 之旁边那批 cc 不会给你做的工具：跨仓库、AST 级 diff、结构化经验记忆、代码健康度评分。

模型在变强，harness 的"约束"在变薄，但 harness 的"能力"必须变厚。手脚多的 agent，不一定比手脚少的 agent 模型更强 —— 它只是能去到那些手脚少的 agent 去不了的地方。

> "精准 tools 即精准上下文。"

记住这句就够了。

---

*相关阅读：*
- [`README.md`](../README.md) — 项目总览、安装、三种使用模式
- [`AGENTS.md`](../AGENTS.md) — 仓库内 agent 工作约定
- [`code-hacker.agent.md`](../code-hacker.agent.md) — VS Code Custom Agent system prompt 与 tool 绑定
- [`subagents.yaml`](../subagents.yaml) — 4 个专用 subagent（Git Archaeologist / Code Scanner / Code Reviewer / Workspace Coordinator）的定义
