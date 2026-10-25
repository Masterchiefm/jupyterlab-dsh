# JupyterLab 3 / 4 双版本兼容：点击卡片卡死问题

> 日期：2026-10-25
> 影响版本：labextension 按 JupyterLab 3 构建、运行在 JupyterLab ≥ 4 的场景
> 状态：已修复（单 bundle 同时支持 JupyterLab 3.3+ 与 4.x）

[English summary below](#english-summary)

---

## 现象（Symptoms）

在 JupyterLab 4.x 上，Launcher 里能正常显示 `deepseek-harness` 卡片，
但**一点击，整个 JupyterLab 页面立即卡死无响应**（包括其他标签页、左侧栏、
菜单——所有 UI），只能杀掉浏览器标签页恢复。浏览器控制台无报错，服务端
日志也无异常。

启动时控制台其实有被忽略的线索：

```
Unsatisfied version 2.7.5 of shared singleton module @lumino/widgets (required ^1.19.0)
Unsatisfied version 4.5.6 of shared singleton module @jupyterlab/launcher (required ^3.3.2)
...
```

## 根因（Root Cause）

**lumino v1 → v2 的破坏性 API 变更：`Iterator.next()` 的返回语义。**

| | lumino v1（JupyterLab 3.x） | lumino v2（JupyterLab 4.x） |
|---|---|---|
| `iter.next()` 返回值 | 元素本身 `T`，耗尽时 `undefined` | `{ value, done }`（IteratorResult 对象） |
| 判空写法 | `w === undefined` | `result.done === true` |

本插件的"单例标签页复用"逻辑按 v1 惯用法编写：

```ts
const iter = app.shell.widgets('main');
for (let w = iter.next(); w !== undefined; w = iter.next()) {
  if (w.id === WIDGET_ID) { existing = w; break; }
}
```

在 lumino v2 下 `iter.next()` 永远返回一个**对象**，`w !== undefined`
恒为真 → **死循环**，而且循环体里永远匹配不到 `w.id`（`w` 是
`{value, done}` 而不是 Widget），连 `break` 都救不了它。

由于 dsh GUI 是**同源 iframe**，它和 JupyterLab 主页面共用同一个浏览器
渲染进程主线程。这个 JS 死循环跑在主线程上，于是**整个页面**（包括
iframe 内外一切 UI）冻结——不只是插件自己。这就是"能显示、一点就死"的
完整解释。

注意：插件构建时的依赖声明（`@jupyterlab/* ~3.3`、`@lumino/widgets
^1.32`）不会阻止它被装到 JupyterLab 4 上。webpack module federation 的
shared singleton 解析在宿主版本不满足 requiredVersion 时**只是告警并继续
使用宿主模块**，并不报错失败——所以 jl3 构建产物能在 jl4 里加载起来，
然后死于运行时 API 差异。

## 定位方法（How it was found）

1. 排除服务端：`dsh web` 子进程正常、`/dsh/` 反代（含 token 交换、cookie
   重写）curl 实测 3ms 响应 200。
2. 无头 Chromium + Playwright 复现：`click()` 卡在 "performing click
   action" 直到 30s 超时——主线程被同步占死。
3. 在页面**健康时**通过 CDP 预先 attach 调试器（`Debugger.enable`），卡死
   后 `Debugger.pause()` 抓住空转调用栈，栈顶正是插件 bundle 里
   `execute` 处理器中的这个 for 循环（用 `Debugger.getScriptSource` 对准
   列号确认）。

> 经验：对"页面卡死"类问题，CDP `Debugger.pause` 能直接给出空转点，
> `wait_for_function("() => true", timeout)` 的 rAF 轮询是可靠的主线程
> 响应性探针（主线程忙则 rAF 永远不触发）。

## 修复策略（Fix Strategy）

两层修复，缺一不可：

### 1. 代码层：同时兼容两种 next() 契约 + 硬上限保护

```ts
function findExistingDshWidget(app: JupyterFrontEnd): Widget | undefined {
  const iter = app.shell.widgets('main');
  for (let guard = 0; guard < 4096; guard++) {
    let cur: any = iter.next();
    if (cur === undefined || cur === null) return undefined;      // v1: 耗尽
    if (typeof cur === 'object' && 'done' in cur) {               // v2: IteratorResult
      if (cur.done) return undefined;
      cur = cur.value;
      if (cur === undefined || cur === null) return undefined;
    }
    if ((cur as Widget).id === WIDGET_ID) return cur as Widget;
  }
  return undefined;  // 兜底：契约再变也不允许死循环
}
```

要点：

* **不假设单一契约**——按返回值形态（原始值 vs `{value, done}` 对象）
  自适应，v1/v2 都正确；
* **硬上限**——即使未来 API 再变，最坏情况是功能降级（单例复用失效），
  绝不再冻结整个页面。这是本次事故最重要的教训：*涉及迭代的第三方接口，
  循环必须有界*。

### 2. 构建层：让 shared singleton 在两代宿主上都"满足版本"

`@jupyterlab/builder` 生成 module federation 的 `requiredVersion` 时，
用的是**builder 自带 `@jupyterlab/application` 的核心依赖区间**，而不是
扩展自己在 `dependencies` 里写的范围（见 `@jupyterlab/builder` 源码
`extensionConfig.js`）。用 jl3 builder 构建就要求 `^3.x`，用 jl4 builder
构建就要求 `^4.x`——单 bundle 必然在另一代宿主上产生大量
"Unsatisfied version" 告警。

builder 提供了扩展侧的覆盖开关 `jupyterlab.sharedPackages`。本插件显式
声明双版本区间：

```jsonc
// labextension/package.json
"jupyterlab": {
  "extension": true,
  "outputDir": "jupyterlab-dsh-dist",
  "sharedPackages": {
    "@jupyterlab/application": { "requiredVersion": "^3.3.0 || ^4.0.0" },
    "@jupyterlab/apputils":    { "requiredVersion": "^3.3.0 || ^4.0.0" },
    "@jupyterlab/coreutils":   { "requiredVersion": "^5.3.0 || ^6.0.0" },
    "@jupyterlab/launcher":    { "requiredVersion": "^3.3.0 || ^4.0.0" },
    "@jupyterlab/translation": { "requiredVersion": "^3.3.0 || ^4.0.0" },
    "@jupyterlab/ui-components": { "requiredVersion": "^3.3.0 || ^4.0.0" },
    "@lumino/widgets":         { "requiredVersion": "^1.32.0 || ^2.0.0" }
  }
}
```

`dependencies` 同步写成双区间（`"^3.3.0 || ^4.0.0"` 等），文档化支持范围。
webpack 的 semver 支持 `||` 区间，宿主版本落在任一分支即视为满足：
JupyterLab 3.3+（lumino 1.x）与 4.x（lumino 2.x）都干净解析、零告警，
且始终使用宿主模块（singleton），不会出现双份 Widget 类的 instanceof
问题。

构建环境固定用 jl4 工具链（builder ^4 + typescript ~5.3），构建出的
bundle 同时服务两代宿主；运行时 API 差异由上面的自适应代码兜住。
另外补了 `.yarnrc.yml`（`nodeLinker: node-modules`）：jlpm/yarn 3 默认
PnP 链接模式会让 `tsc -b` 找不到模块。

### 服务端（Python 侧）无需改动

`jupyterlab_dsh/` 只依赖 `jupyter_server>=1.8` 与 tornado 的稳定接口
（`JupyterHandler`、`web.authenticated`、`url_path_join`、
`WebSocketHandler`），jupyter_server 1.x（jl3）与 2.x（jl4）均兼容，
已实测。

## 验证（Verification）

无头 Chromium + Playwright 端到端验收（打开 Launcher → 点击卡片 → rAF
探针测主线程响应性 → 检查 iframe `readyState` 与 GUI 文本）：

| 宿主 | 点击耗时 | 主线程 | dsh GUI |
|---|---|---|---|
| JupyterLab 4.5.6 (py3.13, jupyter_server 2.17) | 0.06s | 全程响应 | complete，完整渲染 |
| JupyterLab 3.6.8 (py3.12, jupyter_server 2.21) | 0.05s | 全程响应 | complete，完整渲染 |

（修复前在 jl4 上：`click()` 30 秒超时、主线程冻结。）

## 给维护者的提醒（Notes for maintainers）

跨 JupyterLab 3/4 的 prebuilt 扩展，除本例的 `Iterator.next()` 外，
还应注意 lumino v1 → v2 的其他破坏性变更（信号签名、`CommandRegistry`
类型、DOM 工具函数位置等）。判断准则：**凡是对宿主 shared 模块（尤其
`@lumino/*`）返回值做迭代的代码，要么用 `for...of`（两代都实现了
Symbol.iterator 时），要么写双契约适配 + 有界循环**。若用到 v2 独有
API，则需要按宿主版本拆分 bundle 或运行时特性检测。

---

## English summary

**Symptom.** On JupyterLab 4.x the launcher card rendered, but clicking it
froze the entire JupyterLab tab (same-origin iframe + main page share the
renderer main thread).

**Root cause.** The extension was built for JupyterLab 3 (lumino v1) where
`iterator.next()` returns `T | undefined`. lumino v2 (JupyterLab 4) changed
`next()` to return an `{value, done}` IteratorResult object. The singleton
tab check `for (let w = iter.next(); w !== undefined; w = iter.next())`
therefore never terminated — an infinite loop on the UI thread.

**Fix.** (1) A dual-contract, guard-capped widget-scan helper that works on
both lumino generations and can never spin forever. (2) Build-layer: a
single bundle declaring dual shared ranges via `jupyterlab.sharedPackages`
(`"^3.3.0 || ^4.0.0"` etc.), because `@jupyterlab/builder` derives
`requiredVersion` from its own core packages, not the extension's
dependency ranges — without the override the other generation logs
"Unsatisfied version" warnings. Build with the jl4 toolchain; ship one
bundle for both. Server extension needed no changes (stable
`jupyter_server` 1.x/2.x APIs only).

**Verified.** E2E click tests on JupyterLab 4.5.6 and 3.6.8: click <0.1s,
main thread responsive throughout, dsh GUI fully loaded in both.
