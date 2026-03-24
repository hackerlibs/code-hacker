# Code Hacker - VS Code Custom Agent

一个媲美 Claude Code 的 VS Code 自定义 Chat Agent，基于 4 个 MCP Server + VS Code 内建工具，覆盖文件操作、Git、代码分析、持久记忆和网络访问。

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                   Code Hacker Agent                      │
│               (code-hacker.chatmode.md)                  │
├─────────┬───────────┬────────────┬────────────┬─────────┤
│ MCP 1   │  MCP 2    │  MCP 3     │  MCP 4     │ VS Code │
│ 文件系统 │  Git 操作  │  代码智能   │  持久记忆   │  fetch  │
│         │           │            │            │ (内建)   │
├─────────┼───────────┼────────────┼────────────┼─────────┤
│filesystem│git_tools  │code_intel  │memory_store│  网络    │
│  .py    │   .py     │   .py      │   .py      │  访问    │
└─────────┴───────────┴────────────┴────────────┴─────────┘
```

## 项目文件

```
.
├── filesystem.py              # MCP 1: 文件读写、编辑、搜索、命令执行
├── git_tools.py               # MCP 2: Git 全套操作
├── code_intel.py              # MCP 3: AST 分析、符号提取、依赖图
├── memory_store.py            # MCP 4: 持久记忆 + 思考板
├── code-hacker.chatmode.md    # Agent 定义（系统提示词 + 工具绑定）
├── .vscode/
│   └── mcp.json               # MCP 服务器注册（仅供参考）
└── README.md
```

## 前置要求

- **VS Code** 1.99+
- **GitHub Copilot Chat** 扩展
- **Python** 3.10+
- **Git**

```bash
pip install mcp
```

可选（推荐）：安装 [The Silver Searcher](https://github.com/ggreer/the_silver_searcher) 以获得更快的代码搜索：

```bash
# macOS
brew install the_silver_searcher
# Ubuntu/Debian
sudo apt install silversearcher-ag
# Termux
pkg install the_silver_searcher
```

## 安装与配置

### 第一步：注册 MCP 服务器

MCP 服务器**不会自动启动**，必须在 VS Code 用户设置中手动注册。

打开 `settings.json`（`Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`），添加以下配置：

```json
{
  "mcp": {
    "servers": {
      "filesystem-command": {
        "type": "stdio",
        "command": "python",
        "args": ["/你的绝对路径/filesystem.py"]
      },
      "git-tools": {
        "type": "stdio",
        "command": "python",
        "args": ["/你的绝对路径/git_tools.py"]
      },
      "code-intel": {
        "type": "stdio",
        "command": "python",
        "args": ["/你的绝对路径/code_intel.py"]
      },
      "memory-store": {
        "type": "stdio",
        "command": "python",
        "args": ["/你的绝对路径/memory_store.py"]
      }
    }
  }
}
```

> **将 `/你的绝对路径/` 替换为实际路径**，例如 `/home/user/vscode-custom-agents/filesystem.py`

### 第二步：验证 MCP 连接

添加后，VS Code 底部状态栏会显示 MCP 服务器状态。确保 4 个服务器都显示为已连接。

如果未连接，检查：
- Python 路径是否正确（可能需要用 `python3` 替代 `python`）
- `mcp` 包是否已安装
- 文件路径是否为绝对路径

### 第三步：放置 Agent 文件

将 `code-hacker.chatmode.md` 放在你要使用的**项目根目录**下。

> 关键配置 — `tools` 字段必须使用 `服务器名/*` 通配符格式：
> ```yaml
> tools: ["filesystem-command/*", "git-tools/*", "code-intel/*", "memory-store/*", "fetch"]
> ```
> 其中 `fetch` 是 VS Code 内建工具，不需要额外配置。

### 第四步：开始使用

1. 在 VS Code 中打开包含 `code-hacker.chatmode.md` 的项目
2. 打开 Copilot Chat 面板（`Ctrl+Shift+I`）
3. 在顶部**模式选择器**中选择 **Code Hacker**
4. 开始对话

> **排查：** 如果模式选择器中没有 Code Hacker：
> - 确认 VS Code >= 1.99
> - 确认 `.chatmode.md` 文件在工作区根目录
> - 重启 VS Code

## 全部工具清单

### filesystem-command (12 个工具)

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容，支持 utf-8/gbk/gb2312 等编码 |
| `read_file_lines` | 读取指定行范围，适合大文件 |
| `write_file` | 写入文件 |
| `append_file` | 追加内容到文件 |
| `edit_file` | **精确字符串替换**（传入 old_string → new_string） |
| `find_files` | glob 模式递归搜索文件 |
| `search_files_ag` | 正则搜索文件内容（类似 ripgrep） |
| `list_directory` | 列出目录内容 |
| `get_file_info` | 文件详细信息（大小、时间、权限） |
| `create_directory` | 递归创建目录 |
| `get_current_directory` | 获取工作目录 |
| `execute_command` | 执行系统命令（已屏蔽危险命令） |

### git-tools (11 个工具)

| 工具 | 说明 |
|------|------|
| `git_status` | 工作区状态 |
| `git_diff` | 查看变更（支持 staged） |
| `git_log` | 提交历史 |
| `git_show` | 查看提交内容或特定版本的文件 |
| `git_branch` | 列出分支 |
| `git_create_branch` | 创建新分支 |
| `git_checkout` | 切换分支/恢复文件 |
| `git_add` | 暂存文件 |
| `git_commit` | 提交 |
| `git_stash` | 暂存管理（push/pop/list） |
| `git_blame` | 逐行追溯修改者 |

### code-intel (5 个工具)

| 工具 | 说明 |
|------|------|
| `analyze_python_file` | Python AST 深度分析（类、函数、导入、文档） |
| `extract_symbols` | 提取符号定义（Python/JS/TS/Java/Go/Rust） |
| `project_overview` | 项目全景（目录树、语言分布、入口点、配置） |
| `find_references` | 跨文件查找符号引用 |
| `dependency_graph` | 文件导入/被导入关系分析 |

### memory-store (7 个工具)

| 工具 | 说明 |
|------|------|
| `memory_save` | 保存记忆（支持分类和标签） |
| `memory_get` | 读取特定记忆 |
| `memory_search` | 搜索记忆（按关键词/分类/标签） |
| `memory_list` | 列出所有记忆 |
| `memory_delete` | 删除记忆 |
| `scratchpad_write/read/append` | 临时思考板（复杂推理用） |

### VS Code 内建

| 工具 | 说明 |
|------|------|
| `fetch` | 获取网页/API 内容 |

## 使用示例

```
你: 帮我分析这个项目的架构
→ Agent 调用 project_overview → find_files → analyze_python_file → 输出分析报告

你: 把所有 print 语句改成 logging
→ Agent 调用 search_files_ag 找到所有 print → read_file_lines 确认上下文 → edit_file 逐个替换

你: 这个 bug 是谁引入的？
→ Agent 调用 git_blame → git_show → 定位引入 bug 的提交

你: 记住：这个项目的 API 要走 /api/v2 前缀
→ Agent 调用 memory_save 持久化这个决策

你: 查一下 FastAPI 的中间件文档
→ Agent 调用 fetch 获取文档内容
```

## 与 Claude Code 能力对比

| 能力 | Claude Code | Code Hacker |
|------|------------|-------------|
| 文件读写 | Read/Write | read_file / write_file |
| 精确编辑 | Edit (old→new) | edit_file (old→new) |
| 行范围读取 | Read (offset/limit) | read_file_lines |
| 文件搜索 | Glob | find_files |
| 内容搜索 | Grep (ripgrep) | search_files_ag |
| 命令执行 | Bash | execute_command |
| Git 操作 | Bash + git | 11 个专用 git 工具 |
| 代码分析 | 靠 LLM 理解 | AST 解析 + 符号提取 |
| 项目概览 | Agent 探索 | project_overview 一键生成 |
| 持久记忆 | memory 文件系统 | memory_store 结构化存储 |
| 网络访问 | WebFetch/WebSearch | fetch (VS Code 内建) |

## 自定义

### 添加文件类型白名单

编辑 `filesystem.py` 中的 `ALLOWED_EXTENSIONS`。

### 修改命令黑名单

编辑 `filesystem.py` 中的 `BLOCKED_COMMANDS`。

### 调整 Agent 行为

编辑 `code-hacker.chatmode.md` 中的系统提示词。

### 添加新工具

创建新的 MCP Server `.py` 文件，在 VS Code settings.json 中注册，然后在 chatmode 的 `tools` 中添加 `"新服务器名/*"`。

## License

MIT
