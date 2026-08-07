# 贡献说明文档设计

日期：2026-08-05

## 目标

在仓库根目录新增 `README.md`，用简洁的中文说明本项目如何在保持本地
Agent 控制权的前提下，为 `Nexus-Editor` 准备和贡献改动。

## 设计

README 只描述当前仓库已经存在的能力，不新增脚本、权限或自动化流程，包含
以下四个部分：

1. **项目目的**：说明本仓库是本地协作层，使用 `Nexus-Editor/`
   Submodule 保持上游代码边界和本地 Agent 的自主控制。
2. **Submodule 协作**：说明在 Submodule 内创建分支、提交改动、推送到个人
   Fork 并向上游提出 PR；父仓库只记录 Submodule 的 Gitlink 提交指针。
3. **OpenCode 子 Agent**：列出两个项目级 `mode: subagent` 定义文件、模型
   和可用于创建 Task 的 Agent 名称。
4. **远程仓库授权**：链接 `GITHUB_APP_AUTH.md`，说明授权使用仓库本地
   Git 配置中的凭据助手，运行时读取仓库外私钥并生成短时凭据；不复制、提交
   或持久化私钥、JWT 和 Installation Token。

## 约束

- 不在 README 中重复 App ID、Installation ID 或其他可变授权明细；这些信息
  以 `GITHUB_APP_AUTH.md` 为唯一说明位置。
- 不把任何密钥、Token 或凭据内容写入仓库。
- 不修改全局 Git 或 OpenCode 配置。
- 保留现有 `.DS_Store` 未跟踪文件，不将其纳入本次变更。

## 验证

- 检查 README 中的路径、Agent 名称和 Submodule URL 与仓库实际内容一致。
- 使用 `git diff --check` 检查格式。
- 使用 `git status --short` 确认只包含预期文档变更。
