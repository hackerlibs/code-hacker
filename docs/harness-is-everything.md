# Harness 即一切：模型不是不够聪明，而是是否有足够有用的tools

> "从第一性来说，模型不是不够聪明，而是是否有足够有用的tools。"
> —— Steve.桥布施, AI 生产力训练营群聊, 2026-05-20

> "精准 tools 即精准上下文。"
> —— Steve.桥布施

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

## 二、为什么"harness"这个词最近才出现 —— 从 CoT 到 Harness 的简史

群聊里那句"模型越强，harness 越松"，其实是站在 **过去四年 LLM 推理范式不断打补丁** 的肩膀上才能说出来的。如果不交代这段史，"harness 是什么"这个问题就会悬空。这一节把这段被压缩的发展史展开。

### 1. CoT（Chain-of-Thought, 2022）—— "让模型先把思路写出来"

Wei et al. 那篇著名的 *"Let's think step by step"*。核心发现：
**只要 prompt 让模型先输出推理过程再给答案，准确率就显著上升。**

CoT 是纯 prompting，没有 tool，没有外部世界，没有反思。模型像一个独自做题的学生，把草稿写在卷子上。

**局限**：

- 一条思维链一旦走偏，就一路错到底。
- 算不了的就是算不了 —— 没有计算器，没有解释器，没有文件系统。
- 推理过程是纯文本生成，没有"行动"。

### 2. ToT（Tree-of-Thoughts, 2023）—— "不只是一条链，是一棵树"

Yao et al. 把 CoT 升级成树搜索：每一步生成多个候选思路，估值、剪枝、回溯。本质是把单调的"思维链"扩展成"思维空间的搜索"。

ToT 解决了 CoT "一条道走到黑"的问题，但仍然有两个根本局限：

- 还是关在自己脑子里 —— 没有 tools，不能跟外部世界交互。
- 计算开销随分支指数爆炸。

ToT 之后大家发现：**与其在脑子里假想分支，不如直接出去走一走 —— 把行动结果作为下一步思考的输入。** 这就引出了 ReAct。

### 3. ReAct（Reasoning + Acting, 2022/2023）—— Agent 的真正起点

Yao et al. 另一篇关键论文。范式：

```
Thought  → 我应该 grep 一下这个函数在哪被用
Action   → grep "send_email" -r src/
Observation → src/notify.py:42, src/billing.py:117
Thought  → 看起来 billing.py 的调用更可疑，读一下
Action   → read_file("src/billing.py", lines=110-130)
Observation → ...
Thought  → 找到了，问题是这里没 catch SMTPException
Final Answer → ...
```

ReAct 干了两件石破天惊的事：

1. **引入"行动"作为一等公民。** 模型不再只生成 token，它还能调用工具改变世界。
2. **把"观察"塞回上下文。** 行动产生的反馈成为下一轮推理的输入，思维与世界形成闭环。

**这是"agent"这个词从科幻变成工程的拐点。** 后来所有 coding agent —— cc、codex、`code-hacker` —— 主循环本质上都是 ReAct 的变种。

但 ReAct 还有问题：**它只关心"当前这一步该干什么"，不关心"我刚才那条路是不是死路"。** 模型可能在同一个错误判断上反复磨。

### 4. Reflexion / 反思机制（2023）—— "失败之后写一段总结，再来一次"

Shinn et al. 的 Reflexion 引入显式反思。当一次任务失败（测试没过、目标没达成），模型不是简单重试，而是先写一段"反思笔记"塞进上下文，再开下一次尝试。

反思机制 + ReAct = **跨 trial 的学习能力**。模型在一次会话里就能从错误中成长，而不需要重新训练。

到这里，agent 已经具备了：

- **推理**（CoT/ToT）
- **行动**（ReAct）
- **反思**（Reflexion）

但还差一件事：**这些机制怎么落到产品里？** 每个项目都自己实现一套 ReAct loop、自己写一套反思 prompt、自己维护一套工具注册表 —— 重复劳动。

### 5. Harness 时代（2024–2026）—— 把上述全部"沉淀"成基础设施

"Harness"（脚手架/挽具）这个词的流行，标志着 agent 从论文范式进入工程范式：

> Harness = 把 **ReAct loop + Reflection + Planning + Tool 编排 + 持久记忆 + 子任务委派** 全部沉淀成一个可复用的 framework，模型只负责"想"和"决策"，其余由 framework 处理。

cc、codex、Cursor Agent 都是 harness。`deepagents` 的 `create_deep_agent` 也是 harness。它们做的事高度收敛 —— 这就是群聊里为什么会说 **"真正的壁垒不在 cc"**，因为 harness 这一层已经被收敛成几乎一样的形状了。

那么到了 harness 时代，差异在哪？两处：

- **模型的推理能力**（这一层 Anthropic / OpenAI 在卷）
- **Tools 的精准度和领域覆盖**（这一层 *用户* 在卷 —— 这就是 `code-hacker` 在做的事）

CoT → ToT → ReAct → Reflexion → Harness，每一步都是把"原本写在 prompt 里、要求模型自己学会的能力"，**外化成 framework 提供的基础能力**。模型解放出来去做更复杂的判断。

**这条进化轴的箭头是：内化在 prompt → 外化在 framework。** 越外化，越稳定，越可组合。

---

## 三、`create_deep_agent` 把上述演进编译成了什么

理解了上面那段史，再看 `web_app.py` 里那几行就完全是另一种感受：

```python
# web_app.py:311
agent = create_deep_agent(
    model=model,
    tools=all_tools,             # 62 个 MCP tools — ReAct 的 Action 空间
    subagents=subagents,         # 4 个 subagent — 任务委派
    memory=["./AGENTS.md"],      # 持久指令 — Reflection 的长期记忆
    backend=FilesystemBackend(   # 文件后端 — scratchpad 与 memory 的物理存储
        root_dir=EXPERT_DIR
    ),
    system_prompt=SYSTEM_PROMPT,
)
```

`create_deep_agent` 来自 `deepagents`（langchain 团队基于 Claude Code 的"深度智能体"工作流抽象出来的开源 framework）。它做的事，正是把前一节所有历史成果**一次性焊接到主 loop 里**：

| 历史阶段 | 在 `create_deep_agent` 里的对应物 | 在 `code-hacker` 里的体现 |
|---|---|---|
| **CoT** —— 让模型写思路 | 默认 system prompt 鼓励"想清楚再动手" | `code-hacker.agent.md` 里 *"Understand First, Act Second"* |
| **ToT** —— 计划 / 多步搜索 | `TodoListMiddleware` —— 模型写 todo、勾选完成 | TUI / Web 上能看到的"任务列表"流 |
| **ReAct** —— Tool 调用循环 | 主 loop 本体：interleave tool_call ↔ observation | 62 个 MCP tool 的统一调度 |
| **Reflexion** —— 反思 / 长期记忆 | `MemoryMiddleware` + `FilesystemBackend` | `./AGENTS.md` 加载为长期记忆；`memory_store` MCP server 把跨会话经验持久化到 CozoDB |
| **任务委派** —— sub-agent | `subagents=[...]` 参数 | `subagents.yaml` 里的 4 个专家：git_archaeologist / code_scanner / code_reviewer / workspace_coordinator |

也就是说，**`create_deep_agent(...)` 这一行调用背后，是过去四年 agent 范式演进的全部沉淀**。你不需要再手写 ReAct loop，不需要再手动管理 reflection buffer，不需要再为每个子任务起一个新的 chat session —— 这些都被 `middleware` 化了。

### 重点看两个 middleware

#### A. `TodoListMiddleware` —— 把 ToT 的"计划"工业化

模型可以在过程中调 `write_todos([...])` 把任务拆成可勾选的子项，下一步调 `update_todo(id, status="done")`。这等价于把 ToT 的搜索树拍平成一份可见、可改、可恢复的清单。

这件事过去要么靠 system prompt 反复强调，要么靠用户手动拆任务。**`create_deep_agent` 直接把它做成 tool**，模型自然就会用。这就是"约束从 prompt 退到 framework"的典型例子。

#### B. `MemoryMiddleware` + `FilesystemBackend` —— 把 Reflexion 工程化

`memory=["./AGENTS.md"]` 的含义是："这个文件，每次 agent 启动都加载到上下文里作为长期指令"。`FilesystemBackend` 则给了一个工作目录，让 agent 可以读写跨 turn 持久的文件。

但 `code-hacker` 把这一层又向前推了一步 —— **`memory_store.py` 这个 MCP server 是一个真正的 CozoDB 后端、带分类 finder 的结构化经验库**：

- Reflexion 论文里说"agent 写反思笔记进 context" → `code-hacker` 里说"agent 把可复用经验写进 CozoDB，下次用 `find_pipeline(query=...)` 精准取出"。
- 论文里的反思是 *会话级* 的，下次新会话就丢了 → `memory_store` 是 *跨会话、跨项目、可分类、按使用频次排序* 的。

这是一个层层加码的关系：
**`create_deep_agent` 提供了 generic 的 memory 能力 → `code-hacker` 在 generic memory 之上又叠了一个领域专用的 memory server。** 群聊里的"精准 tool 即精准上下文"在这里被推到了极致 —— 连记忆本身都按领域分类了。

### Subagents = 任务委派的工业化

`create_deep_agent` 提供了一个内建的 `task` 工具：主 agent 可以把一个子任务委派给某个 subagent，subagent 用自己的 model（通常更便宜的 Haiku）+ 自己的 system_prompt + 自己的 tool 子集 跑，跑完只返回一份**压缩过的报告**给主 agent。

这是一个非常重要的设计：

- **主 agent 的 context 不会被 subagent 内部那一堆 grep / read 噪声塞满。**
- **subagent 用便宜的模型也能跑得不错，因为它的 tool 子集精准、任务范围小。**

回到群聊那句"精准 tool 即精准上下文"—— subagent 机制就是**把这条原则递归地应用到 agent 自己身上**：每个 subagent 都是更小、更聚焦、tool 更精的"专属 mini agent"。

看 `subagents.yaml` 里 `git_archaeologist` 的定义：它只拿到 7 个 tool（git_status / git_diff / git_log / git_show / git_branch / git_blame / read_file / search_files_ag），不能写文件、不能改 git 状态、不能跨项目操作。**约束 = 安全 + 聚焦 = 更好的输出。**

### 一图总结

```
        ┌─────────────────── create_deep_agent ────────────────────┐
        │                                                          │
        │   ┌─ CoT/ToT  →  TodoListMiddleware     ──┐              │
        │   ┌─ ReAct    →  主 tool-call loop      ──┤   主 agent   │
        │   ┌─ Reflexion→  MemoryMiddleware       ──┤   (Sonnet)   │
        │   ┌─ Delegate →  subagents + task tool  ──┘              │
        │                                                          │
        │                       │                                   │
        │                       ▼                                   │
        │            ┌──────────────────────┐                       │
        │            │  62 个 MCP tools     │  ← code-hacker 加的   │
        │            │  (6 个 MCP server)   │     "领域专属手脚"     │
        │            └──────────────────────┘                       │
        │                       │                                   │
        │                       ▼                                   │
        │   ┌──────────┬────────────┬────────────┬─────────────┐    │
        │   │ Git Arch │ CodeScanner│ Reviewer   │ Workspace   │    │
        │   │(Haiku)   │ (Haiku)    │ (Haiku)    │ Coordinator │    │
        │   │ 7 tools  │ 8 tools    │ 10 tools   │ (Haiku)     │    │
        │   └──────────┴────────────┴────────────┴─────────────┘    │
        │             4 个 subagent，各自 tool 子集精准              │
        └──────────────────────────────────────────────────────────┘
```

可以这样总结：

> **`create_deep_agent` 把 CoT/ToT/ReAct/Reflexion 编译成了 middleware；`code-hacker` 在这些 middleware 之上，又叠了一层专门为"写代码"打磨的 62 个 tool 和 4 个 subagent。**

前者是 *agent 范式* 的工业化，后者是 *领域能力* 的工业化。两者合起来，才是群聊里说的那个"模型越强 harness 越松，但 tools 必须越来越精准"的完整答案。

---

## 四、code-hacker 是怎么"加手脚"的

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

## 五、harness 设计的几条第一性原理

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

## 六、两个反例：cc 自带 tools 不够的两类场景

抽象的"精准 tool 即精准上下文"再讲一万遍，也不如看两个真实的反例：**逆向工程** 和 **训练模型**。它们看起来风马牛不相及，但底层是同一种困难 —— 你都是在跟一个不会主动开口告诉你内部状态的黑盒打交道，靠"读、试、修、再试"逼近答案。

### 6.1 反例一：逆向工程

群聊里 @GeniusVczh 提到他给微软的 toolchain 加了 debugger 和内存泄漏分析 tools，并且吐槽"微软居然不做"。这是个完美的反例：

- 模型能力：足够 —— Claude 完全能理解 WinDbg 命令、能读 dump、能推理内存布局。
- 模型 tools：不够 —— cc 默认只能 `Read` / `Bash`，你让模型在终端里手敲 WinDbg 然后用 grep 解析输出？loop 长得吓人，每一步都可能错。
- 加上专门的 debugger MCP server 之后：模型一次 `debug_get_call_stack(pid=123)` 拿到结构化 stack，一次 `debug_find_leak(snapshot_id=...)` 拿到候选泄漏点。**问题从"模型解不出"变成"模型 5 步解出"**。

**同一个模型，加上对的 tools，行为差异不是百分比级别的，是数量级级别的。**

### 6.2 反例二：训练模型本身也是逆向工程

更深一层的反例 —— 也是更近的：**让 agent 帮你训练/改进模型**。如果你直接拿 cc 去迭代一个 LLM 训练脚本，你会发现它根本不知道往哪改，改了之后也不知道好不好，几轮就开始转圈。

为什么？因为训练模型本身就是一个**逆向工程**问题：

- 你没有 ground truth 告诉你"这次 attention 加 dropout 该用 0.1 还是 0.15"。
- 你唯一的信号是 **跑一次 → 看 metric → 再决定下一步**。
- 没有"读代码就能想明白"这种近路 —— 模型行为 *只能* 通过实验来探索。

Karpathy 最近开源的 [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) 把这件事做成了一个极其干净的样本。它的 harness 几乎可以当 ML 领域"精准 tool"的范本来读：

1. **极窄的修改面** —— agent 只能改 `train.py`（包含模型 / optimizer / 训练循环），其他文件锁死。修改空间被 harness 压扁成一个轴。
2. **固定时间预算** —— 每次实验固定跑 5 分钟，不管硬件多猛多挫。这把"实验"标准化成可比较单元，每小时约 12 次 → 一夜约 100 次实验。
3. **单一评估指标** —— validation bits-per-byte，一个数字。agent 不需要解读复杂报表，看一个 metric 就能判断"留下还是丢弃"。
4. **`program.md` 是人写的研究方向** —— harness 不替 agent 决定"今晚研究什么"，但替它处理掉所有"怎么把实验跑起来、怎么比较结果、怎么留下好的改动"这些事务性工作。
5. **过夜自驱**循环 —— agent 一晚上跑 ~100 次实验，留下有效改动、丢掉无效改动，第二天早上你看一份 diff 加一份指标曲线。

把这套机制拆开看，它其实就是把前面讲过的 **CoT/ToT/ReAct/Reflexion → Harness** 这条演进，落地到了 ML 研究这个具体场景：

| 抽象机制 | autoresearch 里的具体形态 |
|---|---|
| ReAct 的 Action 空间 | "改 `train.py`" 这一个动作 |
| ReAct 的 Observation | 5 分钟后的 bits-per-byte 数字 |
| Reflexion 的反思 | "上次改 dropout 让 metric 跌了 → 这条路不走" |
| ToT 的 plan / 搜索 | "今晚我要按 program.md 探索这一族 hyper-param" |
| 例子引导 | `program.md` 写明的研究方向 + baseline `train.py` 作为起点 |
| 纠偏机制 | 单一数字 metric + "5 分钟跑完" 的硬时间盒 |
| 循环机制 | 自动重复 ~100 次的 overnight loop |

**注意"例子引导"和"纠偏机制"这两行**。它们就是用户提到的核心：

- **好的例子（baseline + program.md）告诉 agent "起点在哪、方向是什么"** —— 没有这个，agent 在 ML 搜索空间里就是随机游走。
- **纠偏机制（单一指标 + 时间盒）告诉 agent "你刚才那步对不对"** —— 没有这个，agent 不会从错误中收敛，只会一直发明新的错。
- **loop 机制（overnight 自驱）让上述两件事在睡觉时持续发生** —— 没有这个，单步再聪明也只是一次性烟花。

这套东西**不是模型能力**。Sonnet / Opus 已经具备做 ML 研究的推理能力，但你扔一个空白终端给它，它不知道怎么开始、不知道怎么衡量、不知道什么时候停。

> 如果你直接拿 cc 去迭代模型，是不够的。
>
> 不是模型不行 —— 是 cc 没给你 "改 `train.py` + 5 分钟跑完 + 看 bpb + overnight 重复" 这套循环；
> 你必须自己把这套 harness 搭起来，agent 才有得发挥。

这正是 autoresearch 存在的意义：**它就是为"训练模型"这个领域专门定制的 harness**，就像 `code-hacker` 是为"通用编程"专门定制的 harness、@GeniusVczh 的 debugger tools 是为"Windows 逆向"专门定制的 harness 一样。

### 6.3 两个反例合在一起说了什么

把 6.1 和 6.2 并排看，能看出一个更普适的规律：

> 凡是**"靠读源代码无法推理出答案，必须通过实验/观察才能逼近真相"**的任务，cc 自带的 Read/Bash/Grep 一定不够。
> 你必须把"做实验"这件事本身工具化 —— 一个动作、一个观察、一个比较、一个循环。

逆向工程里"做实验"是 `debug_step` / `read_memory(addr)` / `get_call_stack()`；
ML 训练里"做实验"是 `run_train_5min()` / `eval_bpb()` / `keep_or_discard()`。
形式不同，本质相同：**都是把"观察黑盒"这件事压成一个 tool，把"对比 / 纠偏 / 循环"这件事压成一个 harness。**

回到群聊那句"你主要做的东西决定了你要增强的 tools 是什么"——
- 你做 web → 你需要 browser MCP、HTTP 测试 tool
- 你做逆向 → 你需要 debugger / memory MCP
- 你做 ML 研究 → 你需要 autoresearch 这种 train-eval-loop harness
- 你做通用编程 → 你需要 `code-hacker` 这 62 个 tool

**cc 不替你做任何一种，因为 cc 不知道你在做哪种。** 这恰恰是 harness 设计者存在的价值。

---

## 七、对 `code-hacker` 使用者 / 贡献者的几条具体建议

1. **不要把 `code-hacker` 当 "另一个 Claude Code 替代品" 用。** 它的价值在于那 62 个 tool，尤其是 cc 没有的那批（ydiff、multi-project、memory_store 分类 finder）。要让模型真的用上这些 tool —— 例如，你提的问题里直接说"用 ydiff 看看这个 commit"。

2. **加 tool 之前先问：这个 tool 会替模型省掉几个 loop？** 如果答案是 0，这个 tool 多半不该加。每多一个 tool，模型选择 tool 的认知负担就多一点，要克制。

3. **加 tool 之后立刻更新 `code-hacker.agent.md` 和 `README.md`。** Tool 的名字和签名是它给模型的"自我介绍"，文档则是它给模型的"使用场景说明"。两者缺一不可。这就是 `AGENTS.md` 里那条 "When adding a new MCP tool, register it in the relevant server file, then update `code-hacker.agent.md`'s tool listing and `README.md`" 的真正意义。

4. **能用 `edit_file` 就别用 `write_file`。** 这不是性能问题，是 harness 在保护你 —— `edit_file` 强制模型先理解再修改。

5. **遇到模型卡住的时候，第一反应不是"换更强的模型"，而是"我是不是少了一个 tool"。** 群聊那段对话其实就是这句话的展开。

---

## 八、结语

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
