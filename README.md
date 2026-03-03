# CodeOps

## 安装

一键安装（推荐，安装后任意目录可用）：

```bash
curl -fsSL https://raw.githubusercontent.com/ZAaiyan/codeops/main/install.sh | bash
```

验证安装：

```bash
codeops --help
```

一键更新（升级到最新 main）：

```bash
curl -fsSL https://raw.githubusercontent.com/ZAaiyan/codeops/main/install.sh | bash
```

一键更新到指定版本/分支（可选）：

```bash
CODEOPS_REF=v0.1.0 curl -fsSL https://raw.githubusercontent.com/ZAaiyan/codeops/main/install.sh | bash
```

从源码安装（开发/贡献者）：

```bash
cd codeops
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 配置 API Key（OpenAI 或豆包/火山方舟）

推荐：只配置一次（写入本机配置文件），之后直接运行 `codeops`：

```bash
codeops auth login
```

也可以使用环境变量（仅对当前 shell 会话生效）：

```bash
export OPENAI_API_KEY=xxx
```

使用火山引擎豆包（火山方舟 OpenAI 兼容接口）：

```bash
export ARK_API_KEY=xxx
```

如果你在项目目录下放置 `.codeops.yaml`，可以配置豆包的 Base URL 与模型接入点（Endpoint ID）。

推荐做法：复制示例文件后再改（不要把个人配置提交到仓库）：

```bash
cp .codeops.yaml.example .codeops.yaml
```

```yaml
base_url: https://ark.cn-beijing.volces.com/api/v3
model: YOUR_ENDPOINT_ID
max_iterations: 12
temperature: 0
```

可选配置：

```yaml
planner_model: gpt-4o-mini
max_context_chars: 60000
```

## 运行

进入交互模式：

```bash
codeops
```

单次执行：

```bash
codeops run "fix this bug"
```

交互示例（默认启用“任务规划 + 分步执行”）：

```text
CodeOps > 重写这个模块以提高性能
plan:
1. 分析性能瓶颈
2. 提出重构策略
3. 生成并应用 patch
4. 运行测试验证
```

解释文件：

```bash
codeops explain file.py
```

重构目录：

```bash
codeops refactor src/ --yes
```

## 交互指令

- `/exit` 退出
- `/clear` 清空历史
- `/files` 查看当前目录文件
- `/run <shell command>` 执行安全受限的 shell 命令
- `/plan` 切换/查看计划
- `/undo` 撤销最近一次写入
- `/redo` 重做最近一次撤销
- `/apply` 开启自动写入（相当于 `--yes`）

## 安全策略

- shell 命令默认走白名单（可用 `CODEOPS_SHELL_ALLOWLIST` 追加或用 `CODEOPS_SHELL_ALLOW_ALL=1` 放开）
- `rm/chmod/chown` 等危险命令需要确认：在交互模式用 `/apply`，或在非交互模式使用 `--yes`
- 禁止 `sudo`、递归删除、越权访问工作目录外的绝对路径

## 长期记忆

- 会持久化保存最近的对话片段与文件变更历史（用于后续上下文与 `/undo`/`/redo`）
- 默认位置：`~/.config/codeops/state/`
