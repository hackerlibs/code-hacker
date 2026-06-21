# 用最小作用原理写一个 MCP：模型的那双手，该怎么造

> "模型不是不够聪明，而是是否有足够有用的 tools。"
> —— 上一篇《Harness 即一切》留下的那句话

> "自然用最少的动作完成事情。"
> —— 物理学的最小作用原理

上一篇《Harness 即一切》讲清了一件事：**agent 之间真正的差距，越来越不在模型本身，而在你给模型装上的那双手——tools。** 而在今天，"给模型造一双手"的标准做法，就叫 **MCP（Model Context Protocol）**。

这一篇就回答一个具体问题：**MCP 到底是什么、它为什么这么设计、以及——你怎么用半小时写出一个能用的 MCP？**

全文用本项目 `code-hacker` 里两个真实的 MCP server（`filesystem.py` / `git_tools.py`）当解剖样本，配套一个 skill，让你照着就能写出自己的第一个 MCP。

---

## 一、一句话讲清 MCP

LLM 本质是一个**文本生成器**。它关在自己的脑子里，看不到你的文件、跑不了你的代码、读不到你的 git log。

MCP 就是那条把模型接到真实世界的标准电线：

```
            ┌─────────────┐         MCP 协议          ┌──────────────┐
   模型  ←→ │   Host /    │ ←──── tools/list ────→   │  你的 MCP    │
（脑子）    │   Client    │ ──── tools/call ─────→   │  Server      │
            │ (CC / IDE)  │ ←──── 结构化结果 ────    │ (你写的代码) │
            └─────────────┘                           └──────────────┘
                                                            │
                                                            ▼
                                              文件 / git / 数据库 / API / ...
                                                       （真实世界）
```

- **模型** 负责"想"——它产出一个意图："我想读 `train.py` 这个文件"。
- **MCP server** 负责"做"——它把意图变成真实动作（`open().read()`），再把世界的状态**结构化地**喂回模型。

> 没有 tools，模型是脱缰的野马，只会"说";
> 有了 MCP，模型才长出手脚，能"读世界的状态、把行动作用回去"。

一个 MCP server 就是一组 **tool** 的集合。写 MCP，本质就是**写 tool**。所以这篇文章真正的主题是：**怎么设计一个好 tool。**

---

## 二、把"最小作用原理"搬到 MCP 设计上

物理学里，自然总是用**最少的动作**完成一件事。这个原则迁移到工程，就是高手的雷达：

- 这个 feature 的最小实现路径是什么？
- 这个 bug 的最小复现是什么？
- 这次能砍掉的最大开销是什么？

写 MCP 时，"最小作用"有一个非常具体、非常省钱的含义：

> **一次 tool 调用，要让模型本来需要绕 5 个来回才能拼出来的信息，一次性拿到。**
> 这就是上一篇说的——**精准 tool 即精准上下文。**

模型的每一次 `Thought → Action → Observation` 来回，都在烧 token、烧延迟、烧它有限的注意力。一个设计得好的 tool，等价于给模型一个更短、更准、更便宜的 prompt。

把 README《Why you not student》里那三件高维武器对照过来，就是 MCP 设计的三条铁律：

| 高维武器 | 写 MCP 时的含义 |
|---|---|
| **最小作用原理** | 一个 tool 一次就把模型要的东西给全，别让它绕来绕去 |
| **最小可运行** | 先写一个 10 行能跑通的 tool，验证链路，再加功能 |
| **"为什么不行"** | 在暴露 tool 之前先想：模型会怎么用错它、怎么把我的机器搞坏 |

下面我们用真实代码，把这三条落到地面。

---

## 三、解剖一个真实的 MCP server

打开 `git_tools.py`,一个完整的 MCP server 只有三段式：

```python
from mcp.server.fastmcp import FastMCP

# ① 起一个 server
mcp = FastMCP(name="git-tools", host="localhost", port=8002)

# ② 用装饰器把一个普通函数变成 tool
@mcp.tool()
async def git_status(repo_path: str = ".") -> str:
    """Show working tree status: staged, unstaged, and untracked files.

    Args:
        repo_path: Path to the git repository (default: current directory)
    """
    return format_result(run_git(["status"], cwd=repo_path))

# ③ 跑起来
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

就这么多。`FastMCP` 帮你做完了所有协议层的脏活——握手、`tools/list`、`tools/call`、序列化。**你只需要写函数。**

但魔鬼在细节里。上面这个 `git_status` 短短几行，藏着四个**写给模型而不是写给人**的设计决定：

### 1. 函数签名 = 给模型的 schema

```python
async def git_status(repo_path: str = ".") -> str:
```

你写的类型注解（`repo_path: str`）和默认值（`= "."`），`FastMCP` 会自动翻译成 JSON Schema，发给模型。模型靠这个 schema 知道："哦，这个 tool 要一个叫 `repo_path` 的字符串，不给的话默认当前目录。"

> **原则：类型签名即契约。** 参数名要让模型一看就懂——`repo_path` 比 `p` 强一万倍。

### 2. docstring = 写给模型的 prompt

```python
"""Show working tree status: staged, unstaged, and untracked files.

Args:
    repo_path: Path to the git repository (default: current directory)
"""
```

这段 docstring **不是给人看的注释，是给模型看的说明书**。模型读它来决定"什么时候该调这个 tool"。所以它必须：

- 第一句话说清"这个 tool 做什么"——一个**核心动词**(show status)。
- `Args` 里解释每个参数,尤其是默认行为。

> **原则:docstring 是 tool 的脸。** 模型选不选你这个 tool,全看这张脸说清楚没有。

### 3. 返回值要"自带上下文"

看 `filesystem.py` 里的 `read_file`,它没有只 `return content`,而是:

```python
return f"File: {file_path}\nSize: {len(content)} characters\n\n{content}"
```

返回里带上了文件名、大小。这样模型在后续推理时,不用再回头问"我刚读的是哪个文件",上下文里已经写着了。这正是**最小作用**——一次返回,信息给全。

> **原则:返回值是下一轮的 prompt。** 让模型读完就知道发生了什么,而不是只拿到一坨裸数据。

### 4. 安全边界 = 护城河(这就是"为什么不行")

这是 `filesystem.py` 最值得学的地方。在真正动手之前,它先问"模型会怎么用错我":

```python
BLOCKED_COMMANDS = {'rm', 'del', 'format', 'mkfs', 'dd', 'shutdown', ...}

def is_safe_path(path: str) -> bool:
    """Check if the path is safe (no directory traversal)."""
    return not any(part.startswith('..') for part in Path(path).parts)
```

每个会改变世界的 tool,动手前都先过一道闸:

```python
@mcp.tool()
async def write_file(file_path: str, content: str, ...) -> str:
    if not is_safe_path(file_path):
        return f"Error: Unsafe file path: {file_path}"
    if not is_allowed_file(file_path):
        return f"Error: File type not allowed: {Path(file_path).suffix}"
    if len(content.encode(encoding)) > MAX_FILE_SIZE:
        return f"Error: Content too large"
    # ...真正写入
```

注意:出错时它**返回一句人话给模型**,而不是抛异常崩掉。模型读到 `Error: Unsafe file path` 就会自己纠正。

> **原则:把模型当成一个聪明但会犯错的实习生。** 给它锋利的工具,但在工具上装好护栏。
> 一个错的动作 × 一个能干的模型 = 把你的机器高效地搞坏。

---

## 四、可以"长在身上"的 MCP 设计原则

招式可以查文档,**原则必须长在身上**。下面这张表,是写任何 MCP 时该在 0.1 秒内调用的反射:

| 表层(招式) | 内核(原则) |
|---|---|
| 给 docstring 写清楚第一句 | **docstring 是写给模型的 prompt,不是注释** |
| 参数起个好名字、给默认值 | **类型签名即契约,默认值即最小作用** |
| 返回里带上文件名/数量/状态 | **返回值是下一轮的 prompt,要自带上下文** |
| 一个函数只干一件事 | **一个 tool 一个核心动词** |
| 把 5 次小查询合并成 1 个 tool | **精准 tool 即精准上下文** |
| 动手前检查路径/大小/命令 | **在工具上装护栏——先想"模型会怎么用错"** |
| 出错 return 一句话,不抛异常 | **错误信息也是给模型的反馈,要能自我纠正** |
| 先写 10 行能跑的,再加功能 | **最小可运行——先验证链路,别一上来造航母** |

> 几条原则,少而锋利,所以能在写代码的当下瞬间调用。
> 这正是 review 一个 AI 写的 tool 时,那 5 秒里你身体调用的东西。

### 一个反例:别把 tool 写成"面"

模型擅长把一个点扩充成面,人类的价值在点的精度。同理,**别把一个 tool 设计成"什么都能干"的瑞士军刀**:

```python
# ❌ 坏味道:一个 tool 吞下整个 git
@mcp.tool()
def git(subcommand: str, args: str) -> str:
    """Run any git command."""   # 等于没给 tool,模型还是在猜
```

```python
# ✅ 好味道:一个 tool 一个核心动词(看 git_tools.py 怎么做的)
@mcp.tool()
def git_status(repo_path: str = ".") -> str: ...
@mcp.tool()
def git_diff(repo_path: str = ".", staged: bool = False) -> str: ...
@mcp.tool()
def git_log(repo_path: str = ".", max_count: int = 20) -> str: ...
```

拆开之后,每个 tool 的 schema 和 docstring 都在**精确地告诉模型它能做什么**。这就是"精准 tool 即精准上下文"的反面教材与正面教材。

---

## 五、最小可运行:半小时写出你的第一个 MCP

理论讲完,上手。遵循**最小可运行**——先写一个 10 行能跑通的,验证链路。

### 第 1 步:装依赖

```bash
pip install "mcp[cli]"      # 或 uv add "mcp[cli]"
```

### 第 2 步:写 server(完整文件,可直接跑)

`weather.py`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="weather", host="localhost", port=8010)

@mcp.tool()
async def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: City name, e.g. "Beijing"
    """
    # 真实场景这里会调天气 API;先用假数据验证链路
    return f"Weather in {city}: Sunny, 23°C, humidity 40%."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

跑起来:

```bash
python weather.py        # 监听 localhost:8010
```

### 第 3 步:接到 Claude Code(或任意 MCP 客户端)

在项目的 `.mcp.json` / `mcp.json` 里加一条:

```json
{
  "mcpServers": {
    "weather": { "url": "http://localhost:8010/mcp" }
  }
}
```

重启客户端,问它"北京天气怎么样",它就会自动调用你的 `get_weather`。

> 这一步跑通的那一刻,你就完成了一次完整的 **Thought → Action(call get_weather) → Observation** 闭环。
> 链路通了,剩下的只是**把假数据换成真 API、把 tool 越加越多**。

### 第 4 步:从"能跑"到"能用"——把第四节那张表过一遍

回头给 `get_weather` 补上:更准的 docstring、输入校验(空城市名怎么办?)、自带上下文的返回、错误时 return 一句话。一个玩具 tool 就长成了一个生产 tool。

---

## 六、配套 skill:让写 MCP 变成"提一嘴"的事

为了让"模型越强,harness 越松"落到实处,本项目配了一个 skill:**`write-mcp`**(在 `.claude/skills/write-mcp.md`)。

它把上面这套流程压缩成了模型可以直接执行的步骤:你只要说一句"帮我写一个查天气的 MCP",Claude(或本项目的 Code Hacker agent)就会:

1. 用 FastMCP 三段式生成 server 骨架;
2. 按"一个 tool 一个核心动词"拆分工具;
3. 自动补上 docstring、类型签名、输入校验、自带上下文的返回;
4. 选一个不冲突的端口,给出 `mcp.json` 接入片段;
5. 给出最小可运行的验证命令。

> 这就是 harness 的自指之美:**我们用一个 skill(harness 的一部分),去更快地造出更多 tool(harness 的另一部分)。**

---

## 七、收尾:tool 是模型的手,而手怎么造,是你的判断

回到最开始那句话——"模型不是不够聪明,而是是否有足够有用的 tools"。

写 MCP 这件事,代码层面 `FastMCP` 已经简单到几行。真正的难度、也是真正的价值,从来不在"怎么写",而在那些 AI 替你答不了的判断:

- **这个 tool 该不该存在?**(还是模型自己用 Bash 就够了)
- **它的边界画在哪?**(给多大的权力,装多厚的护栏)
- **它一次该返回什么?**(怎样才算"精准上下文")

这些判断,正是 README 里说的——**提出一个高维度的、有效的突破点,再让 AI 把它扩充成面。**

> FastMCP 把"写 tool"这件事压平了。
> 但"该给模型造一双什么样的手"——这个问题没有训练数据,只能靠你。

去写你的第一个 MCP 吧。从那个 10 行的 `weather.py` 开始。

---

## 参考

- 本项目源码:`filesystem.py`(文件系统 MCP)、`git_tools.py`(Git MCP)
- 配套 skill:`.claude/skills/write-mcp.md`
- 前文:《Harness 即一切:模型不是不够聪明,而是是否有足够有用的 tools》(`docs/harness-is-everything.md`)
- 思想来源:《Why you not student?》——最小作用原理 / 最小可运行 / "为什么不行"
- 协议规范:Model Context Protocol(modelcontextprotocol.io)
