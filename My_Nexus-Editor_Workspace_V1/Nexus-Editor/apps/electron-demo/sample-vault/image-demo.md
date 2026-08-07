# Image Preview Demo

测试 vault 内本地图片 + Obsidian 风格交互。

## 1. 基本相对路径

最常见的写法，alt 文本会作为 tooltip：

![示例本地图片](img/1.png)

## 2. `|width` 指定宽度（Obsidian 语法）

格式：`![alt|width](url)` 或 `![alt|widthxheight](url)`

![示例本地图片|310](img/1.png)

![示例本地图片|500x200](img/1.png)

## 3. 空 alt 的显式显示

alt 为空时，hover 仍然可以看到 `</>` 和 resize 点：

![](./img/1.png)

## 4. 缺失图片（错误兜底）

下面的路径不存在，应该渲染为红色 "image not found" 提示：

![缺失的图](img/does-not-exist.png)

## 交互说明

- **默认态**：只看到渲染的图片
- **hover**：紫色边框 + 右上 `</>` + 右下紫色 resize 点
- **点 `</>`**：切到编辑态 —— 源码 `![alt|size](url)` 可编辑，下方同时显示预览图
- **编辑态改源码**：预览实时刷新
- **拖右下点**：实时预览新尺寸，松手后 `|width` 自动写回 markdown
- **点图片本身**：不触发编辑（Obsidian 行为）
- **退出编辑**：点击任意其它位置

---

回 [[index]] ｜ 相关代码：`apps/electron-demo/src/renderer/editor-shell.ts` 的自定义 image renderer 和 `packages/core/src/live-preview.ts` 的 image 分支。
