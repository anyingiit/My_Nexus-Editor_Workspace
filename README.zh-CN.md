[English](README.md) · **简体中文**

> 英文版是规范版本。本页与 [README.md](README.md) 不一致时，以英文版为准。

<!-- translation-of: README.md sha256:80144c4f4dc9321a -->

<!-- Source: Best-README-Template BLANK_README (Unlicense) — https://github.com/othneildrew/Best-README-Template -->
<a id="readme-top"></a>

# My_Nexus-Editor_Workspace

一个个人工作区，存档了一次借助 AI 编程代理为 Nexus-Editor 编写并评估贡献用 AGENTS.md 的尝试（该尝试已被放弃）；其中保存着 My_Nexus-Editor_Workspace_V1 快照及其 nexus-eval 评估框架，另有一个未拉取的 Nexus-Editor git 子模块；本仓库自身没有任何可构建的源码。

[![License](https://img.shields.io/github/license/anyingiit/My_Nexus-Editor_Workspace)](LICENSE)

[报告问题](https://github.com/anyingiit/My_Nexus-Editor_Workspace/issues/new?template=bug_report.yml) · [提出需求](https://github.com/anyingiit/My_Nexus-Editor_Workspace/issues/new?template=feature_request.yml)

<details>
  <summary>目录</summary>
  <ol>
    <li><a href="#about-the-project">关于本项目</a></li>
    <li><a href="#getting-started">开始使用</a></li>
    <li><a href="#usage">用法</a></li>
    <li><a href="#contributing">参与贡献</a></li>
    <li><a href="#license">许可证</a></li>
    <li><a href="#contact">联系方式</a></li>
  </ol>
</details>

## 关于本项目

`My_Nexus-Editor_Workspace` 是 anyingiit 的个人存档，记录了一次借助 AI 编程代理（配置见 `.opencode/agent/`）为 [Nexus-Editor](https://github.com/floatboatai/Nexus-Editor) 编辑器项目编写并评估一份分层的、仅供贡献使用的 `AGENTS.md` 的尝试。这次尝试的全部内容都保存在 `My_Nexus-Editor_Workspace_V1/` 目录下，其自身的 `README.md` 记录了该尝试已被放弃；其中的 `nexus-eval/` 子目录则保存了这次尝试用来给自己打分的数据集、评分细则与报告。仓库根目录下的 `Nexus-Editor` 只是一个指向该独立第三方项目的 git 子模块（见 [.gitmodules](.gitmodules)），在本次检出中并未拉取，因此本仓库自身没有任何需要编译或运行的源码。

## 开始使用

### 环境要求

- 一个文本编辑器或 Markdown/JSON 查看器即可，用来阅读存档中的笔记和数据集——这里的内容全部是纯文本。
- 如果你想拉取根目录 `Nexus-Editor` 子模块所指向的代码，还需要支持子模块的 Git；见 [.gitmodules](.gitmodules)。这会拉取另一个独立仓库，阅读本工作区其余内容并不需要它。

### 安装

没有构建步骤，也没有任何属于本仓库自身、需要安装的东西。克隆仓库即可得到这些存档文件：

```sh
git clone https://github.com/anyingiit/My_Nexus-Editor_Workspace.git
cd My_Nexus-Editor_Workspace
```

顶层的 `Nexus-Editor/` 目录会保持为空，除非你另外执行 `git submodule update --init`——那会拉取另一个仓库 `https://github.com/floatboatai/Nexus-Editor`，此步骤完全是可选的。

## 用法

这里没有什么可以运行的；这个工作区的意义在于阅读它记录下来的内容：

```sh
$EDITOR My_Nexus-Editor_Workspace_V1/README.md            # 这次尝试自己写下的状态说明
$EDITOR My_Nexus-Editor_Workspace_V1/nexus-eval/README.md  # 评估框架构建了什么、又是如何打分的
```

## 参与贡献

欢迎参与。[CONTRIBUTING.md](CONTRIBUTING.md) 说明如何提交 issue 或 pull request，[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 说明对所有参与者的行为要求。

请不要在公开的 issue 或 pull request 中报告安全问题。[SECURITY.md](SECURITY.md) 说明了私下报告的方式。

## 许可证

以 MIT 许可证分发。详见 [LICENSE](LICENSE)。

## 联系方式

项目地址：[https://github.com/anyingiit/My_Nexus-Editor_Workspace](https://github.com/anyingiit/My_Nexus-Editor_Workspace)

<p align="right">(<a href="#readme-top">back to top</a>)</p>
