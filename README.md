# CodeOps

## 安装

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

如果你在项目目录下放置 `.codeops.yaml`，可以配置豆包的 Base URL 与模型接入点（Endpoint ID）：

```yaml
base_url: https://ark.cn-beijing.volces.com/api/v3
model: YOUR_ENDPOINT_ID
max_iterations: 12
temperature: 0
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
- `/apply` 开启自动写入（相当于 `--yes`）
