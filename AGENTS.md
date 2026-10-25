# AGENTS.md — 给 AI 编码代理的工作指南

本仓库是 **jupyterlab-dsh**：一个把 [DeepSeek Harness (dsh)](https://github.com/deepseek-ai/deepseek-harness)
Web GUI 集成进 JupyterLab 的混合扩展（Python server extension + TypeScript
federated labextension）。改代码前先读完本文。

## 仓库结构与运行形态

```
jupyterlab_dsh/          # Python server 扩展（pip 包）
  __init__.py            # 入口：注册 handler、拉起 dsh 子进程、DshExtensionConfig 配置项
  manager.py             # DshManager：spawn/守护 `dsh web`，解析 "dsh web:" URL 行取 token/端口
  handlers.py            # /dsh/ 同源反向代理（HTTP + WebSocket）+ /dsh-ext/status
labextension/            # 前端源码（JupyterLab 3.x federated extension）
  src/index.ts           # Launcher "Other" 卡片 + iframe 主区域标签页（单例）
  src/dsh-icon.ts        # 官方鲸鱼图标（#4D6BFE，由 scripts/gen-icon 生成，勿手改 path 数据）
  style/index.css
  jupyterlab-dsh-dist/   # 构建产物（gitignore，打包 wheel 时需要）
etc/jupyter/jupyter_server_config.d/jupyterlab_dsh.json   # 随 wheel 分发的自动启用配置
dist/                    # wheel 输出（gitignore）
```

运行形态：JupyterLab 监听 `0.0.0.0`；dsh **只能**跑在 loopback（上游刻意拒绝
`--host 0.0.0.0`），由本扩展作为子进程托管；浏览器访问 `<base_url>dsh/`，
由 `handlers.py` 反向代理到 `127.0.0.1:<port>`。

## 必须理解的安全模型（改 handlers.py 前必读）

dsh 的浏览器信任边界（见 upstream `packages/client/connection`）：

1. **Host/Origin fence**：请求的 Host 必须是 loopback / 绑定 IP / `--trusted-host`；
   附带的 Origin 必须指向同一 authority；`sec-fetch-site: cross-site` 拒绝。
2. **launch token**：`GET /?token=...` 只在根路径有效，换取 authority 绑定的
   签名 cookie（host-only、`Path=/`、HttpOnly、SameSite=Strict、cookie 名按
   hostname+port 派生）。
3. Jupyter 侧：handler 保留 `@web.authenticated`（Jupyter 会话认证），
   仅对本路径关闭 `_xsrf`（dsh 前端无法携带 Jupyter 的 xsrf token）。

代理的对应做法（勿破坏这些不变量）：

* 转发时 **不设置 Host**（tornado 按 URL 生成 `127.0.0.1:<port>`），并把
  `Origin` 重写为 `http://127.0.0.1:<port>`、剥离 `sec-fetch-*`/`Referer`。
  这样任何外网访问地址都能过 fence，且 cookie 的 authority 校验恒一致。
* token 交换在 `_entry_point()` 服务端完成，token 不进浏览器。
* `Set-Cookie` 的 `Path=/` 重写为 `Path=<base_url>dsh/`（`_rewrite_cookie`）。
* 3xx `Location` 重写为挂载前缀（`_rewrite_location`）。
* 响应剥离 hop-by-hop + `X-Frame-Options`/`Content-Security-Policy`/COOP，
  其余透传；`decompress_response=False` 且透传 `Accept-Encoding`/`Content-Encoding`
  （不要让 tornado 解压又转发压缩头，会得到乱码）。
* WebSocket：`WebSocketHandlerMixin` → tornado `WebSocketHandler`，上游
  `websocket_connect` 带同样 Origin/Cookie 处理；消息 str/bytes 双态转发。

## 构建与依赖陷阱（重要）

* **webpack 必须锁 5.44.0**（`package.json` devDependencies + `resolutions`）。
  `@jupyterlab/builder ~3.3` 配新 webpack 会报
  `processAssets ... reading 'trim'`。
* 构建 Node：实测 Node 24 可用；**yarn/jlpm 联网需**
  `NODE_OPTIONS="--dns-result-order=ipv4first --no-network-family-autoselection"`
  （node≥20 默认地址族自选择在本机 IPv6 环境会 ETIMEDOUT）。国内网络加
  `jlpm config set registry https://registry.npmmirror.com`。
* tsconfig 已开 `skipLibCheck`（新版依赖的 d.ts 与 TS4.1 不兼容，属噪音）。
* lumino 迭代器没有 Symbol.iterator，用 `iter.next()` 手写循环。
* 输出目录：`tsconfig.outDir=lib`；federated 产物在
  `labextension/jupyterlab-dsh-dist/`。

```bash
cd labextension && jlpm install && jlpm run build        # tsc + jupyter labextension build
cd .. && pip wheel . --no-deps -w dist/                  # 打包含前端的 wheel
cp -r labextension/jupyterlab-dsh-dist <prefix>/share/jupyter/labextensions/jupyterlab-dsh  # 免重启热更
```

`/lab` 页面按请求重扫 labextensions：替换产物文件后浏览器刷新即可，
**不需要重启 JupyterLab**。

## 验证清单（改动后至少跑这些）

```bash
# 1) 子进程就绪
curl -s "http://127.0.0.1:PORT/dsh-ext/status?token=..."          # ready:true
# 2) 入口：服务端 token 交换 + cookie Path 重写为 /dsh/
curl -sD - -o /dev/null -c /tmp/cj "http://<外网IP>:PORT/dsh/?token=<jupyter-token>"
grep -i dsh /tmp/cj                                                # Path=/dsh/
# 3) fence：带 dsh cookie POST，期望 401/404（403=fence 失败）
# 4) WS 升级：wscat/curl 到 /dsh/api/remote.mux 期望 101
# 5) 浏览器：Launcher "Other" 行出现 deepseek-harness 卡片 → 点击 → dsh GUI 可用
```

dsh 需要的运行时：Node ≥ 24.9（仅 dsh 进程需要；JupyterLab 侧构建另计）。
DeepSeek API 直连 `api.deepseek.com`；模型 ID 现为 `deepseek-flash`
(V4.1-Flash) 与 `deepseek-v4-pro`，**没有** `deepseek-v4-flash`。

## 约定

* Python 侧兼容 jupyter_server 1.8+（本机 1.15.6 / JupyterLab 3.3.2 开发）。
* 用户可配置：`c.DshExtensionConfig.port / command / autostart`。
* 绝不把 `DEEPSEEK_API_KEY`、dsh launch token、GitHub token 写进仓库文件或日志。
* 图标改动：编辑 `labextension/src/dsh-icon.ts` 的 fill（当前 #4D6BFE），
  或从 upstream `apps/web/public/favicon.svg` 重新生成 path，再走完整构建。
