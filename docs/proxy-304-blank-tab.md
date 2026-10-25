# 关闭 dsh 标签后重开白屏：代理对 304 的处理缺陷

> 日期：2026-10-25
> 影响版本：≤ 0.1.0（server 扩展 `jupyterlab_dsh/handlers.py`）
> 状态：已修复
> 关联：[jupyterlab3-4-compat.md](jupyterlab3-4-compat.md)（前者是前端死循环，本篇是服务端代理缺陷）

---

## 现象（Symptoms）

dsh 标签页正常打开、正常使用。**叉掉标签后再次点击卡片，新标签白屏**；
把白屏标签叉掉、再点一次卡片，又能正常打开。（每次开关都会复现。）

## 根因（Root Cause）

三层因素叠加，缺一不白：

### 1. tornado 对代理响应自动补 ETag，浏览器因此有了可重放的缓存条目

`RequestHandler.finish()` 中：状态 200 的 GET 响应若没有 `Etag`，tornado 会
对**写缓冲区内容做哈希**自动计算 `Etag` 响应头（`set_etag_header()`）。
代理转发的是后端未解压的原始字节（`decompress_response=False`），内容
稳定，因此同一资源的 ETag 恒定 —— 浏览器把 200 + ETag 缓存下来。

### 2. 重开标签时浏览器条件请求，tornado 把响应"降级"为 304

重开 iframe，浏览器对缓存资源发 `If-None-Match`。代理原样转发条件头，
后端照常返回 200 全量内容；写完缓冲区后 tornado 发现
`If-None-Match` 与自动 ETag 匹配，直接 `set_status(304)` 并清空
write buffer。

### 3. 304 路径上 tornado 清理表示头时踩中 `HTTPHeaders` 的雷 → 500

`finish()` 对 204/304 会调用 `_clear_representation_headers()` 清除
`Content-Type` 等表示头。而本代理此前在 `_emit_response` 中对后端响应头
一律 `add_header()`，导致响应里出现**两个** `Content-Type`：

* `text/html; charset=UTF-8` —— `JupyterHandler.set_default_headers()` 的默认值；
* 后端真实类型（`text/javascript` 等）—— 代理 `add_header` 追加。

tornado `HTTPHeaders.add()` 对**已有名称**追加时会
`self._combined_cache.pop(name)`（失效查找缓存），而 `clear_header()` 的
存在性检查 `if name in self._headers` 查的是 `_as_list`、
`__delitem__` 却先 `del self._combined_cache[name]` —— 两个内部字典
状态不一致，于是：

```
KeyError: 'Content-Type'
→ Uncaught exception → 500 Internal Server Error
```

所有被浏览器缓存过的 CSS/JS 资源在重开时逐个重放、逐个 500，
页面自然白屏。**第三次点击能恢复**纯属侥幸：500 响应不可缓存，浏览器
丢弃缓存条目后，再点就是全新的 200，一切正常。

验证证据（修复前）：

```
GET /dsh/assets/vendor-*.js  (200)
  Content-Type: text/html; charset=UTF-8      ← JupyterHandler 默认
  Content-Type: text/javascript; charset=utf-8 ← 代理追加
  Etag: "076fb7b04618f7dbd0127106607a6430160ddff3"

GET 同资源 + If-None-Match → HTTP/1.1 500 Internal Server Error
（jupyter 日志：KeyError: 'Content-Type'）
```

## 修复（Fix）

`jupyterlab_dsh/handlers.py` 的 `_emit_response()` 两处：

1. **无 body 状态码（204/304/1xx）不转发表示头、不写 body。**
   RFC 9110 §9.3.5/§15.4.5 本就禁止 304 携带 `Content-Type` 等
   表示元数据；绕开 tornado 清理逻辑，从根源上不可能再触发
   KeyError。浏览器拿到正常的 304 后直接使用本地缓存副本，
   重开标签的资源加载近乎瞬时。

2. **`Content-Type` 改用 `set_header` 语义**（其余头仍 `add_header` 以保
   持多值正确性）。它会替换 JupyterHandler 的 `text/html` 默认值而不是
   叠加第二个值，200 响应头恢复干净：

```
GET /dsh/assets/vendor-*.js (200)
  Content-Type: text/javascript; charset=utf-8   ← 只剩一个
  Etag: "076fb7b04618f7dbd0127106607a6430160ddff3"

GET 同资源 + If-None-Match → HTTP/1.1 304 Not Modified ✓
```

前端与 Python 其余部分无需改动；这是纯服务端代理修复，对 JupyterLab 3
与 4 同样生效。

## 验证（Verification）

无头 Chromium，真实鼠标点击标签关闭按钮，连续开关三轮：

| 宿主 | 轮次 | 结果 |
|---|---|---|
| JupyterLab 4.5.6 | 开→关→开→关→开 | 每轮 GUI 完整加载，iframe 正确销毁，0 报错 |
| JupyterLab 3.6.8 | 开→关→开 | 同上 |

修复前的同样脚本在第二轮（重开）必然得到 `readyState=complete` 但
`body.innerText` 为空的白屏 iframe，伴随资源 500。

## 给维护者的提醒（Notes for maintainers）

* 在 tornado 里代理/改写响应时，**无 body 状态码（204/304/1xx）是一条
 独立的代码路径**：表示头和 body 的处理都要显式分支，不能与 200 共用。
* `RequestHandler` 上混用 `set_header`/`add_header` 会产生重复头；
  `HTTPHeaders.add()` 对已有名称会使查找缓存失效，此后任何对该头的
  `clear_header()` 都会 KeyError。重复头对浏览器多数场景"无害"，所以
  只在缓存重放这类边缘路径上爆炸——很难第一时间联想到。
* tornado 的自动 ETag 让"代理透传"意外获得了完整的缓存重验证语义。
  这通常是好事（本修复后重开标签秒开），但代理作者必须意识到自己的
  handler 会参与条件请求 / 304 生命周期。

---

## English summary

**Symptom.** Closing the dsh tab and reopening it produced a blank tab;
closing the blank tab and clicking again worked. Reproduced on every
close/reopen.

**Root cause.** Tornado auto-computes an `Etag` over proxied 200 bodies;
on reopen the browser revalidates and tornado flips the response to 304.
For 304, `finish()` strips representation headers, and `clear_header()`
raised `KeyError: 'Content-Type'` because the proxy had `add_header()`ed a
second `Content-Type` (on top of the `text/html` default from
`JupyterHandler`) — `HTTPHeaders.add()` invalidates the lookup-cache entry
for an existing name, while the membership check consults a different
internal dict. Every cached asset 500'd on revalidation → blank page. The
third click "worked" only because uncacheable 500 responses dropped the
browser's cache entries.

**Fix.** `_emit_response()` now (1) forwards no representation headers and
writes no body for 204/304/1xx statuses (as RFC 9110 requires), and
(2) sends `Content-Type` via `set_header` so it replaces the default
instead of stacking a duplicate. Conditional revalidation now yields a
clean 304; reopen is instant from cache.

**Verified.** Three consecutive open/close rounds on JupyterLab 4.5.6 and
two on 3.6.8 — GUI fully loads every time, zero console errors.
