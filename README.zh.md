# jupyterlab-dsh —— 在 JupyterLab 里打开 DeepSeek Harness (dsh)

[English](README.md) | 中文

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

在 JupyterLab 启动器 **Terminal 所在的 "Other" 一行** 增加一张 `deepseek-harness`
卡片（dsh 官方鲸鱼图标），点击即在主区域标签页中打开 dsh 的 Web GUI ——
无论 JupyterLab 监听在哪个地址、你从哪个外网 IP 访问，都能正常使用。

![架构](https://img.shields.io/badge/architecture-same--origin%20reverse%20proxy-4D6BFE)

```
浏览器（任意外网 IP:端口）
   └── JupyterLab (0.0.0.0:8888, Jupyter 会话认证)
         └── /dsh/            ← 本插件：同源反向代理（server 扩展）
               ├── 服务端完成 ?token= 换取签名 cookie（token 不出后端）
               ├── Host/Origin 统一重写为 127.0.0.1:<port>（绕开 dsh fence）
               ├── Set-Cookie Path=/ → Path=/dsh/（cookie 作用域重挂载）
               ├── WebSocket (/dsh/api/remote.mux) 双向隧道（对话/事件流）
               └── 127.0.0.1:<随机端口> ← dsh web 子进程（上游拒绝 0.0.0.0，由扩展托管）
```

启动器 **Other** 一行中的 `deepseek-harness` 卡片（dsh 官方鲸鱼图标，紧邻 Terminal）：

![launcher](docs/launcher.png)

点击卡片后，dsh GUI 以标签页形式嵌入 JupyterLab 主区域（模型 DeepSeek-V4.1-Flash）：

![dsh tab](docs/dsh-tab.png)

## 组件

| 部分 | 位置 | 作用 |
|---|---|---|
| server 扩展 | `jupyterlab_dsh/` (Python) | 拉起/守护 `dsh web` 子进程；`<base_url>dsh/` 反向代理（HTTP+WS）；`<base_url>dsh-ext/status` 健康检查 |
| labextension | `labextension/` (TypeScript) | Launcher "Other" 行卡片 + iframe 主区域标签页（单例复用） |

### 转发安全限制的处理（为什么需要这层代理）

* dsh 的 Host/Origin fence 只接受 loopback / 绑定 IP / `--trusted-host`，
  浏览器从外网地址直接访问会被 403。代理把发往后端的请求落在
  `127.0.0.1`，任何外网访问地址都能通过，且 dsh 的 authority 绑定 cookie
  始终对同一 authority 校验，一处成立、处处成立。
* 启动 token（进程凭据）由服务端持有并完成交换，不出现在浏览器 URL。
* JupyterLab 侧：所有 `/dsh/` 请求仍需已认证的 Jupyter 会话
  （`@web.authenticated`）；仅对本路径关闭 `_xsrf` 校验（dsh 前端无法携带
  Jupyter 的 xsrf token），后端另有 dsh 自己的签名 cookie 把关。
* 代理响应剥离 `X-Frame-Options` / `Content-Security-Policy` / COOP，
  使同源 iframe 可嵌入；其余头部原样透传。

## 环境要求

| 依赖 | 版本 | 用途 |
|---|---|---|
| JupyterLab / jupyter-server | **3.3+ 或 4.x** / ≥1.8 | 宿主 —— 单一构建产物同时兼容两代（已在 3.6.8 与 4.5.6 上实测，见 [docs/jupyterlab3-4-compat.md](docs/jupyterlab3-4-compat.md)） |
| Node.js | **≥ 24.9** | 仅 `dsh` 运行时需要（JupyterLab 本身不需要） |
| @deepseek-ai/dsh | 最新 | dsh CLI（`npm i -g @deepseek-ai/dsh`） |
| DeepSeek API key | — | 安装后在 dsh 界面 **Settings → Models** 中自行填写（也可选环境变量 `DEEPSEEK_API_KEY` 注入） |

模型说明：DeepSeek 官方 API 当前提供的模型 ID 为 `deepseek-flash`
（DeepSeek-V4.1-Flash，默认路由）与 `deepseek-v4-pro`；**不存在**名为
`deepseek-v4-flash` 的 ID。

## 手动安装（在目标服务器上）

### 1. 安装 Node.js ≥ 24.9 与 dsh CLI

```bash
# Node.js 24（官方源；国内可用镜像 https://registry.npmmirror.com/-/binary/node/）
curl -fsSL https://nodejs.org/dist/v24.12.0/node-v24.12.0-linux-x64.tar.xz \
  | tar -xJ -C /opt && ln -sf /opt/node-v24.12.0-linux-x64/bin/{node,npm,npx} /usr/local/bin/

# dsh CLI（国内先切镜像源）
npm config set registry https://registry.npmmirror.com   # 可选
npm install -g @deepseek-ai/dsh
dsh --help    # 应正常输出
```

### 2. 安装本插件

从 [Releases](../../releases) 下载 `jupyterlab_dsh-0.1.0-py3-none-any.whl`，然后：

```bash
pip install jupyterlab_dsh-0.1.0-py3-none-any.whl

# wheel 自动完成：
#   * python 包 jupyterlab_dsh
#   * <prefix>/share/jupyter/labextensions/jupyterlab-dsh   （前端，免编译）
#   * <prefix>/etc/jupyter/jupyter_server_config.d/jupyterlab_dsh.json （自动启用）

jupyter server extension list   # 应看到 jupyterlab_dsh ... enabled
jupyter labextension list       # 应看到 jupyterlab-dsh v0.1.0 enabled OK
```

### 3. 启动、配置 key、验证

```bash
jupyter lab --ip 0.0.0.0 --port 8888 --no-browser --allow-root
```

```bash
# 健康检查（dsh 子进程随 Jupyter 自动拉起，冷启动约 20-30 秒）
curl -s "http://127.0.0.1:8888/dsh-ext/status"        # {"ready": true, ...}
```

浏览器打开 `http://<服务器任意IP>:8888/`，在启动器 **Other** 一行点击
`deepseek-harness` 卡片，然后做一次性配置：

1. **Settings → Models**：粘贴你的 [DeepSeek API key](https://platform.deepseek.com/) 并保存，
   模型路由（`deepseek-flash`）立即可用，无需重启；
2. **Choose workspace**：选择 Jupyter 的 root 目录作为工作区；
3. 之后即可发任务。

> 偏好环境变量的话也可以：`DEEPSEEK_API_KEY=sk-xxx jupyter lab ...`，
> 两种方式任选其一，界面填写是默认推荐方式。

## AI 辅助安装（把下面提示词交给服务器上的 AI 代理）

把这段提示词原样粘给服务器上可执行命令的 AI 助手（如 Claude Code /
ZCode / Codex 等），替换 `<...>` 占位符即可：

```text
请帮我在这台服务器上安装并验证 jupyterlab-dsh（JupyterLab 的 DeepSeek
Harness 集成插件，GitHub 仓库 MasterChiefm/jupyterlab-dsh）。步骤：

1. 从该仓库的 GitHub Releases 下载 jupyterlab_dsh-0.1.0-py3-none-any.whl
   （网络受限时可配置代理或镜像后再下载）。
2. 检查前置条件并补齐：
   - Node.js >= 24.9（只有 dsh 运行时需要；JupyterLab 本身不需要）。
     国内网络可从 https://registry.npmmirror.com/-/binary/node/v24.12.0/
     下载 node-v24.12.0-linux-x64.tar.xz 解压并软链 node/npm/npx。
   - npm 全局安装 @deepseek-ai/dsh（国内先
     npm config set registry https://registry.npmmirror.com）。
   - 确认已安装 JupyterLab 3.x 与 jupyter-server>=1.8。
3. pip 安装该 wheel，然后执行 `jupyter server extension list` 和
   `jupyter labextension list`，确认 jupyterlab_dsh / jupyterlab-dsh 均为 enabled。
4. 启动（不需要任何 API key）：
   jupyter lab --ip 0.0.0.0 --port <端口> --no-browser
5. 验证：
   a. curl http://127.0.0.1:<端口>/dsh-ext/status 直到返回 "ready": true
      （dsh 子进程冷启动约 20-30 秒，注意用已认证的 jupyter token/cookie）；
   b. curl -D - 访问 /dsh/ 入口，确认返回 200 且 Set-Cookie 的 Path 被改写为 /dsh/；
   c. 用浏览器（或无头浏览器）打开 http://<服务器IP>:<端口>/，确认启动器
      Other 一行出现 deepseek-harness 卡片，点击后能加载 dsh 界面。
6. 完成后告诉使用者：DeepSeek API key 由使用者本人在 dsh 界面的
   Settings → Models 页面填写并保存（无需重启，模型 deepseek-flash 立即可用）；
   不要向我要 key，也不要把 key 写入任何文件。
7. 注意：模型用 deepseek-flash（即 DeepSeek-V4.1-Flash）或 deepseek-v4-pro，
   API 中不存在 deepseek-v4-flash 这个 ID。
遇到问题时参考仓库 README 的"手动安装"与 AGENTS.md 的排障说明。
```

## 配置项（jupyter_server_config.py）

```python
c.DshExtensionConfig.port = 0         # dsh 回环监听端口，0=随机（默认，免冲突）
c.DshExtensionConfig.command = ""     # 自定义启动命令，如 "dsh web --no-open --port 3080"
c.DshExtensionConfig.autostart = True # 随 Jupyter 启动 dsh（进程组随 Jupyter 退出）
c.DshExtensionConfig.home = ""        # 本实例专用 dsh home（注入 $DSH_HOME，支持 ~）
c.DshExtensionConfig.agents_home = "" # 专用共享代理配置根（注入 $DSH_AGENTS_HOME）
```

dsh 的工作目录 = Jupyter 的 root_dir。

### 与本机其他 dsh 实例隔离

默认情况下拉起的实例使用环境默认 home（`~/.dsh`）——与本机任何其他方式
启动的 `dsh`/`dsh web`（比如项目目录里的 `npm exec dsh web`）**共享**会话、
凭据和配置，两边互相可见。要让 Jupyter 内嵌的 GUI 使用独立状态，给它指一
个专用 home：

```python
c.DshExtensionConfig.home = "~/.dsh-jupyter"
```

目录按需自动创建；此后 Jupyter 实例不再读写 `~/.dsh` 的任何内容。若想把
已有的 API key 带过去，把 `~/.dsh/.credentials.yaml` 复制一份到新 home 即可
（复制后两份各自独立，换 key 时需分别更换）。

## FAQ（常见部署场景）

### 服务器上只装了 JupyterLab，装这个插件会怎么样？

**JupyterLab 完全不受影响，其它功能照常**：wheel 正常安装、扩展启用、
Launcher 出现卡片；点开卡片会看到明确的错误页（503，"could not start
dsh …"）并每 3 秒自动刷新——因为机器上还没有 `dsh` 命令。后台守护循环
会以指数退避（上限 30 秒）持续重试。之后任何时候补装 Node ≥ 24.9 与
`npm i -g @deepseek-ai/dsh`（无需重启 JupyterLab），最多 30 秒内插件自动
拉起 dsh，报错页自愈为正常 GUI。所以"先装插件、后补依赖"是可行顺序。

### JupyterLab 和 deepseek-harness 都装了，要手动同时启动两者吗？

**不需要，只启动 JupyterLab 一条命令**。`autostart=True`（默认）时扩展
自动把 `dsh web` 作为子进程拉起（约 20–30 秒就绪）；dsh 崩溃自动重启
（刷新标签页即可重连，签名密钥持久在 `$DSH_HOME`，旧会话 cookie 仍有效）；
Jupyter 退出时 dsh 被整组回收，不留孤儿进程。

**不要自己再手动跑一个 `dsh web` 给插件用**——扩展必须自己拉起子进程，
才能从其 stdout 解析本次启动的 launch token 并完成服务端交换；手动起的
实例对插件不可见。

### dsh 运行在别的端口，该如何处理？

* **想让插件自己拉起的 dsh 用固定端口**（默认 0 = 随机回环端口）：
  ```python
  c.DshExtensionConfig.port = 3080   # 插件用 dsh web --no-open --port 3080 拉起
  ```
  前提是端口空闲；被占用时 dsh 起不来，插件显示错误页并持续重试。
* **已有自己手动/系统服务跑着的 dsh 实例在别的端口**：不用处理，两者
  天然共存——插件实例默认用随机回环端口，永不冲突；你的 dsh 继续按原样
  用，插件在 JupyterLab 里另起独立实例（`$DSH_HOME` 存储共享，会话按进程
  区分）。插件不支持"代理到外部已有 dsh 实例"，因为拿不到那个实例的
  进程 token，无法完成浏览器会话交换。
* **dsh 不在 PATH 或需要自定义启动参数**：
  ```python
  c.DshExtensionConfig.command = "/opt/node24/bin/dsh web --no-open --port 3080"
  ```

注意：配置了固定端口且自己的 dsh 也跑同一端口时，两者会抢端口（后启动
的失败重试）——此时改回 `port = 0` 或给两者分不同端口。

## 从源码构建（改代码时才需要）

```bash
cd labextension && jlpm install && jlpm run build   # tsc + jupyter labextension build
cd .. && pip wheel . --no-deps -w dist/
```

构建细节与依赖陷阱（webpack 必须锁 5.44.0、node DNS 参数等）见 [AGENTS.md](AGENTS.md)。

## 已验证

* JupyterLab 监听 `0.0.0.0`，从外网 IP 访问 `/lab`、`/dsh/`（服务端 token
  交换、cookie `Path=/dsh/` 重写）、API POST（fence 通过）、WebSocket 升级
  `/dsh/api/remote.mux`（101 建链）全部通过；Launcher 出现
  `deepseek-harness` 卡片，点击后 iframe 正常加载 dsh GUI 并可执行任务。
* 同一构建产物在 **JupyterLab 3.6.8** 与 **JupyterLab 4.5.6** 上通过端到端
  点击验收（无头 Chromium）：点击 <0.1s 完成、UI 主线程全程响应、dsh GUI
  完整启动。背景与根因分析：[docs/jupyterlab3-4-compat.md](docs/jupyterlab3-4-compat.md)。

## License

MIT © MasterChiefm
