# Code Hacker - VS Code Custom Agent

一个基于 MCP (Model Context Protocol) 的 VS Code 自定义 Chat Agent，提供文件系统操作和代码探索能力。

## 项目结构

```
.
├── filesystem.py              # MCP 服务器，提供文件系统工具
├── code-hacker.chatmode.md    # VS Code 自定义 Agent 定义
├── .vscode/
│   └── mcp.json               # MCP 服务器注册配置
└── README.md
```

## 前置要求

- **VS Code** 1.99+
- **GitHub Copilot Chat** 扩展
- **Python** 3.10+
- Python 依赖：

```bash
pip install mcp
```

可选：安装 [The Silver Searcher](https://github.com/ggreer/the_silver_searcher) 以启用 `search_files_ag` 工具：

```bash
# macOS
brew install the_silver_searcher

# Ubuntu/Debian
sudo apt install silversearcher-ag

# Termux
pkg install the_silver_searcher
```

## 可用工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容，支持 utf-8、gbk、gb2312 等多种编码 |
| `write_file` | 写入内容到文件 |
| `append_file` | 追加内容到文件 |
| `list_directory` | 列出目录内容，可选显示隐藏文件 |
| `get_file_info` | 获取文件详细信息（大小、时间戳、权限） |
| `execute_command` | 执行系统命令（已屏蔽 rm、format 等危险命令） |
| `get_current_directory` | 获取当前工作目录 |
| `create_directory` | 递归创建目录 |
| `search_files_ag` | 使用 ag 搜索代码，支持正则、文件类型过滤 |

## 安全机制

- **路径安全**：阻止目录遍历攻击（`..`）
- **文件类型白名单**：仅允许操作 `.txt`、`.py`、`.js`、`.json`、`.md`、`.yaml` 等常见文本文件
- **文件大小限制**：单文件最大 10MB
- **命令黑名单**：屏蔽 `rm`、`del`、`format`、`mkfs`、`dd`、`shutdown`、`reboot` 等危险命令
- **命令超时**：默认 30 秒超时

## 安装与配置

### 第一步：安装 Python 依赖

```bash
pip install mcp
```

### 第二步：在 VS Code 设置中注册 MCP 服务器

MCP 服务器**不会自动启动**，需要手动添加到 VS Code 的用户设置中。

打开 VS Code 设置（`Ctrl+,`），搜索 `mcp`，或直接编辑 `settings.json`（`Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`），添加：

```json
{
  "mcp": {
    "servers": {
      "filesystem-command": {
        "type": "stdio",
        "command": "python",
        "args": ["/你的绝对路径/filesystem.py"]
      }
    }
  }
}
```

> **注意**：`args` 中必须填 `filesystem.py` 的**绝对路径**。

添加后，VS Code 会在底部状态栏显示 MCP 服务器状态，点击可查看是否已连接。

### 第三步：放置 Agent 定义文件

将 `code-hacker.chatmode.md` 放在项目根目录（已包含在本仓库中）。

该文件中 `tools` 字段使用 `filesystem-command/*` 通配符引用 MCP 服务器提供的所有工具：

```yaml
tools: ["filesystem-command/*"]
```

### 第四步：使用 Agent

1. 在 VS Code 中打开本项目
2. 打开 Copilot Chat 面板（`Ctrl+Shift+I`）
3. 在聊天面板顶部的**模式选择器**中，选择 **Code Hacker**
4. 开始对话，Agent 会自动调用文件系统工具完成任务

> 如果模式选择器中没有出现 Code Hacker，检查：
> - VS Code 版本 >= 1.99
> - `code-hacker.chatmode.md` 在当前打开的工作区根目录下
> - MCP 服务器状态是否为"已连接"（底部状态栏）

### 示例对话

```
你: 分析一下当前项目的结构

Code Hacker: [调用 list_directory, read_file 等工具，输出项目分析报告]

你: 搜索所有包含 "TODO" 的 Python 文件

Code Hacker: [调用 search_files_ag 搜索，返回匹配结果]

你: 创建一个新的配置文件 config.yaml

Code Hacker: [调用 write_file 创建文件]
```

## 自定义

### 添加允许的文件类型

编辑 `filesystem.py` 中的 `ALLOWED_EXTENSIONS`：

```python
ALLOWED_EXTENSIONS = {'.txt', '.py', '.java', '.js', '.json', ...}
```

### 修改命令黑名单

编辑 `filesystem.py` 中的 `BLOCKED_COMMANDS`：

```python
BLOCKED_COMMANDS = {'rm', 'del', 'format', 'mkfs', 'dd', 'shutdown', 'reboot', 'halt', 'poweroff'}
```

### 修改 Agent 行为

编辑 `code-hacker.chatmode.md` 中的系统提示词来调整 Agent 的行为风格和工作方式。

## License

MIT
