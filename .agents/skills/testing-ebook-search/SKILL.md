---
name: testing-ebook-search
description: 端到端测试电子书聚合搜索（server.py + index.html）。当验证搜索结果渲染、被拦/超时的可抓取源是否降级为「打开原站搜索」入口时使用。
---

# 测试电子书聚合搜索

## 启动
- 服务：`python server.py`，默认 `http://127.0.0.1:5178`。
- 浏览器打开后点顶部「电子书」切换到电子书模式。

## 核心被测行为：可抓取源被拦时降级为原站入口
背景：笔趣阁(xiunews) 等可抓取源在连续请求后会被目标站**按 IP 在防火墙层封禁**（`ping` 通但 TCP 端口 `connect` 超时，`curl:(28)`）。修复前，`search_xiunews` 被拦时静默返回 `[]`，该源既不出结果也不进 `blocked` 列表，用户看到「搜不到」。修复后，失败的可抓取源被加入 `blocked`，前端渲染为「{name}：打开原站搜索 ↗」。

相关代码：
- `server.py` `api_book_search`：用 `failed` 集合收集抓取异常的源，追加到 `blocked`。
- `server.py` `_xiunews_search_round` / `search_xiunews`：所有策略均因网络错误失败时抛 `SourceUnavailable`（区别于「200 空结果」）。
- `index.html`：`bBlocked = data.blocked` 渲染为 `.sources` 区的 `pill-link` 链接。

## 验证步骤（搜索 `斗破苍穹`）
1. 在书名框输入搜索词（输入中文见下方「坑」），点「搜索」。
2. 等待约 25s（笔趣阁分支多策略超时预算）。
3. 断言：
   - 「搜索结果」区出现「共 N 条」（安娜的档案，N≈30）。
   - `.sources` 区出现「笔趣阁 xiunews：打开原站搜索 ↗」入口（修复前此入口缺失）。
   - 该入口 href 形如 `http://www.xiunews.com/modules/article/search.php?searchkey=%B6%B7%C6%C6%B2%D4%F1%B7`（GBK 编码的 searchkey，由 `_book_search_url` 按源的 `search_charset` 生成）。
   - 点击入口在新标签打开原站搜索页。

> 注意：Devin 测试环境的机房 IP 通常**也**被 xiunews 封禁，所以「被拦」分支天然可复现；但也因此**无法**验证「服务端真正抓到笔趣阁结果」的正常路径——那需要住宅 IP / 代理。若某天 xiunews 不再封禁本环境，可改用一个仍被封的源，或临时把目标改成不可达地址来复现降级分支。

## 坑 / 经验
- **浏览器里输入中文**：`xdotool type`/computer `type` 直接打中文常常打不进输入框（搜索按钮仍 disabled）。改用剪贴板：`printf '%s' '斗破苍穹' | DISPLAY=:0 xclip -selection clipboard`（需要先 `apt-get install -y xclip`），再点输入框 `ctrl+a` `ctrl+v`。
- **快速核对 href/编码**：computer 工具回传的精简 DOM 可能把长 href 截断；可把页面 HTML（工具会落盘到 `/tmp/page_html_*.html`）用 grep 看完整 `searchkey=...`。
- **直接验证后端**：`curl 'http://127.0.0.1:5178/api/book/search?q=斗破苍穹'`，检查 `count` 和 `blocked[]`（应含 xiunews 及其 GBK 链接）。
- **窗口最大化**（录屏前）：`wmctrl -r :ACTIVE: -b add,maximized_vert,maximized_horz`；若刚跑过缩放/缩小窗口的测试，记得 `ctrl+0` 复位缩放。

## Devin Secrets Needed
无。纯本机服务测试，不需要任何 secret / 登录。
