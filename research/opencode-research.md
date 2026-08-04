# OpenCode 调研

## 来源

- GitHub 仓库：`anomalyco/opencode` — <https://github.com/anomalyco/opencode>
- 主页：<https://opencode.ai>
- 官方文档：<https://opencode.ai/docs>
- npm 包：`opencode-ai` — <https://www.npmjs.com/package/opencode-ai>

仓库元数据（来自 GitHub API，2026-07-30）：

- 描述："The open source AI coding agent." / "开源的 AI Coding Agent。"
- 协议：MIT
- 主要语言：TypeScript
- Stars：~191k，Forks：~24k
- 默认分支：`dev`
- 最新 Release：`v1.18.9`（2026-07-28）

## 定位

OpenCode 是一款**开源的 AI Coding Agent**，既可以在终端以 CLI 形式运行，也有桌面端应用（Beta）。用户可以把它安装到本地，直接在项目目录里调用它来读写代码、运行命令、分析代码库等。

## 主要形态

| 形态 | 说明 |
|------|------|
| CLI | `npm i -g opencode-ai`、`brew install opencode`、scoop、choco、pacman、nix、mise 等 |
| 桌面应用 | macOS / Windows / Linux 安装包（`.dmg`、`.exe`、`.deb/.rpm/AppImage`） |
| 终端 UI | 交互式命令行界面 |

## Agent 模式

OpenCode 内置了可通过 `Tab` 切换的 Agent：

- **build**（默认）：完整权限，适合实际开发改动。
- **plan**：只读模式，默认拒绝文件修改，运行 bash 前会询问，适合分析陌生代码库或规划改动。
- **general**：内部子 Agent，用于复杂搜索和多步任务，也可以在消息中用 `@general` 调用。

## 与我（pi 中的 AI 助手）的核心区别

| 维度 | OpenCode | 当前 pi 中的 AI 助手 |
|------|----------|----------------------|
| **本质** | 需要安装到本地的开源软件 / CLI / 桌面应用 | 运行在 `pi` coding agent harness 里的 AI 助手服务 |
| **使用方式** | 用户在终端输入 `opencode` 启动交互，或运行桌面应用 | 通过对话界面直接调用工具 |
| **代码所有权** | 开源（MIT），可自建、自托管、魔改 | `pi` 是第三方 harness；模型能力由后端提供 |
| **文件操作** | 直接操作本地项目文件；`plan` 模式默认只读，`build` 模式可写 | 直接操作当前工作目录文件，使用 `read` / `edit` / `write` / `bash` 等工具 |
| **交互界面** | 终端 UI / 桌面 GUI | 对话式 + 工具调用反馈 |
| **扩展方式** | 插件 / SDK / 自定义配置（参考其 `sdks/`、`packages/`） | `pi` 的 skills、extensions、custom tools |
| **Agent 模式** | build / plan / general 三种内置 Agent | 依赖 `pi` 的 skills 与工具组合，没有硬编码的 build/plan 切换 |

## 结论

OpenCode 更像一个**完整的、可安装的开源 Coding Agent 产品**（终端 + 桌面）；而当前我更像一个**嵌入在 `pi` harness 中的 AI 编程助手**，通过对话 + 工具调用来完成代码任务。两者都能读写文件、运行命令、分析代码库，但形态、部署方式和生态定位不同。
