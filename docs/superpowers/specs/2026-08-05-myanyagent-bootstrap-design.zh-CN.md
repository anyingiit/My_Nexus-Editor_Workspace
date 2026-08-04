# MyAnyAgent 跨设备 Bootstrap 设计审阅版

日期：2026-08-05

## 1. 目标

当前设备上的 GitHub App 能力依赖 `.git/`：本地 Git 身份、credential
helper 和相关配置都不会随仓库迁移。

本方案要把这些能力拆分为可版本控制的脚本和文档，使其他设备在具备外部私钥后，可以通过一次 bootstrap 重建当前仓库的本地配置，同时满足：

- 普通 Git 提交显示为 `MyAnyAgent[bot]`。
- HTTPS push 使用 MyAnyAgent GitHub App installation token。
- token、JWT 和私钥不进入仓库、不进入远程 URL、不写入系统 credential store。
- 不修改全局 Git 配置，不影响其他项目。
- bootstrap 可以重复执行而不会产生重复配置。

## 2. 当前已确认的身份信息

- App：`MyAnyAgent`（slug：`myanyagent`）
- Client ID：`Iv23lioD363YBpJJB9QE`
- App ID：`4483813`
- Installation ID：`151195329`
- 目标仓库：`anyingiit/My_Nexus-Editor_Workspace`
- GitHub bot：`myanyagent[bot]`
- bot user ID：`312959697`
- bot commit email：`312959697+myanyagent[bot]@users.noreply.github.com`
- 外部私钥默认路径：`~/.secrets/myanyagent.2026-08-04.private-key.pem`

上述 Client ID、App ID、Installation ID 和 bot email 不是私钥；真正的私钥必须在每台设备上通过安全渠道单独提供。

## 3. 将新增的仓库文件

### `scripts/myanyagent-credential-helper.cjs`

这是可迁移、可版本控制的 Git credential helper，职责单一：

- 读取 Git credential protocol 的 `get` 请求。
- 只接受 `github.com` 和目标仓库路径。
- 从当前仓库的 local config 读取本机私钥路径。
- 使用 RS256 JWT 请求短期 installation token。
- 只向 Git 的 credential consumer 输出 `username=x-access-token` 和 token。
- 不打印 token，不写入文件，不调用系统 credential store。
- 请求其他仓库时拒绝返回凭据。

### `scripts/bootstrap-myanyagent.sh`

这是跨设备入口脚本，职责包括：

- 定位当前 Git 仓库根目录。
- 检查 Git、Node.js、HTTPS `origin` 和外部私钥。
- 校验私钥可读且格式有效。
- 写入当前仓库的 `.git/config`。
- 清除当前仓库继承的 credential helper，并安装 tracked helper。
- 执行不打印 token 的认证 smoke test。
- 支持重复执行，结果保持一致。

### `GITHUB_APP_AUTH.md`

补充其他设备的使用说明、私钥前置条件和安全边界。

## 4. 当前仓库的本地配置

bootstrap 只写当前仓库的 `.git/config`，不会使用 `--global`：

```text
user.name=MyAnyAgent[bot]
user.email=312959697+myanyagent[bot]@users.noreply.github.com
user.useConfigOnly=true
commit.gpgsign=false
credential.useHttpPath=true
myanyagent.privateKey=<本机私钥绝对路径>
credential.helper=
credential.helper=!node <当前仓库>/scripts/myanyagent-credential-helper.cjs
```

`commit.gpgsign=false` 只作用于当前仓库。原因是现有 GPG key 属于
`anyingiit`，不是 MyAnyAgent bot。继续用人类 key 签署 bot commit 会造成身份混淆；如果未来需要 Verified bot commit，应为 bot 准备专属签名方案。

## 5. 跨设备使用流程

其他设备需要先通过安全渠道放置私钥，然后在仓库根目录执行：

```bash
MYANYAGENT_PRIVATE_KEY="$HOME/.secrets/myanyagent.2026-08-04.private-key.pem" \
  ./scripts/bootstrap-myanyagent.sh
```

脚本不会生成或下载私钥，也不会从仓库恢复私钥。私钥缺失时应直接失败，不应配置一个看似成功但不能认证的半成品环境。

## 6. 安全边界

- 私钥只存在设备外部路径，不进入 Git 工作树。
- JWT 只在 helper 进程内存中存在。
- installation token 只在 Git credential 流程内传递。
- token 不写入 `.git/config`、系统 keychain 或 remote URL。
- helper 对非目标仓库 fail closed。
- bootstrap 不修改全局 Git 身份、签名或 credential 设置。
- 使用 HTTPS remote；SSH remote 不属于本方案范围。

## 7. 验证标准

- `sh -n scripts/bootstrap-myanyagent.sh` 通过。
- `node --check scripts/myanyagent-credential-helper.cjs` 通过。
- bootstrap 可以在当前设备重复执行。
- 目标仓库 credential 请求返回 `x-access-token` 和短期 token，但终端不显示 token 内容。
- 非目标仓库请求不会得到凭据。
- 当前仓库的 author/committer 是 `MyAnyAgent[bot]`。
- 全局 `~/.gitconfig` 内容保持不变。
- GitHub 页面中的新提交显示为 `myanyagent[bot]`。

## 8. 当前审阅状态

本文件是设计审阅版。bootstrap 脚本和 tracked credential helper 尚未在本轮创建；确认设计后再进入实施计划和代码修改阶段。

## 9. 审阅重点

- 是否接受“私钥必须在其他设备上单独配置，不能随仓库同步”。
- 是否接受当前仓库关闭人类 GPG 签名。
- 是否接受只支持 HTTPS remote，不自动改写 SSH remote。
- 是否接受 bootstrap 只修改当前仓库 `.git/config`。
- 是否接受当前本地已有提交与 bootstrap 新提交一起推送到远程 `main`。
