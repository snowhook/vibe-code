# AIKit —— 通用「模型列表 / 连通性检测」接口

给任何前端复用的一组 HTTP 接口，跑在门户后端 `routing-server`（`https://gr.czyuma.com/api/aikit/*`）。
目的就两件事：**拿这把 Key 能不能通**、**通了能拉出哪些模型**。前端不用再自己处理各家不同的响应结构。

- 全部 `POST`，`Content-Type: application/json`
- 响应带 `Access-Control-Allow-Origin: *`，**任何域名/来源都能直接调用**（包括本地 `file://` 之外的页面、别的站）
- 日志与响应里**不落任何 Key**（只回上游错误原文）
- 限流：每 IP 60 秒 30 次（超了 429）；请求体 ≤ 1 MiB（超了 413）

---

## 1. 三个端点

| 端点 | 作用 | 是否访问上游 |
|---|---|---|
| `POST /api/aikit/providers` | 列出预设服务商（id、baseUrl、可用列表路径、是否支持服务端代持 Key） | 否 |
| `POST /api/aikit/models` | 拉模型列表（顺带就是连通性检测） | 是 |
| `POST /api/aikit/test` | 只测连通，不要求有列表接口 | 是 |

### 请求体（`models` / `test` 通用）

```json
{
  "provider": "ark",              // ark | openai | gemini | qwen（可省略，改传 baseUrl）
  "apiKey":   "你的密钥",          // 传 "server" 表示用服务器代持的 Key（目前仅 qwen）
  "baseUrl":  "https://...",      // 可选，覆盖预设；主机必须在白名单内
  "model":    "doubao-seedream-5-5-261028"  // 可选，仅探针用
}
```

### 返回体

```json
{
  "ok": true,          // 整体是否可用
  "auth": true,        // Key 是否被上游接受（探针模式下才判定）
  "via": "models",     // models | probe
  "count": 22,
  "models": [{"id": "qwen-image-3.0-pro", "label": ""}],
  "tries": [{"url": "...", "status": 200, "body": "..."}],   // 每一次尝试的原始结果，便于自查
  "error": "…"          // 失败时给上游原文
}
```

判定规则：先按 `listPaths` 逐个试，`200` 且解析出模型 → `via:"models"`，`ok:true`。
拿不到列表时用**空 prompt 探针**打生图接口：`401/403` → `ok:false, auth:false`（Key 问题）；`400/422` → `ok:true, auth:true`（鉴权通过，只是参数/模型不对）。
这样能把「Key 填错」和「模型名不对」分开，不再只有一句「生成失败」。

---

## 2. 预设服务商

| id | 服务商 | baseUrl | 列表路径 |
|---|---|---|---|
| `ark` | 豆包 / 火山方舟 | `https://ark.cn-beijing.volces.com` | `/api/v3/models?PageSize=100` → `/api/v3/endpoints?PageSize=100`（接入点会带出 `ep-…` 及其背后模型名） |
| `openai` | OpenAI | `https://api.openai.com` | `/v1/models` |
| `gemini` | Gemini | `https://generativelanguage.googleapis.com` | `/v1beta/models?key={KEY}` |
| `deepseek` | DeepSeek | `https://api.deepseek.com` | `/models` → `/v1/models`（返回带 `name` 友好名，会一起带出） |
| `qwen` | 通义 / 阿里云 | `https://token-plan.cn-beijing.maas.aliyuncs.com` | `/compatible-mode/v1/models` → `/api/v1/models` |

`deepseek` 没有生图接口，探针改打 `/chat/completions`（body 由预设里的 `probeBody` 提供，`{MODEL}` 会被替换成 `model` 参数），所以它同样能区分「Key 无效(401)」和「参数/模型不对(400)」。

`qwen` 支持 `apiKey:"server"`：由服务器代持（读 `~/.openclaw/openclaw.json` 的 `qwenTokenPersonalProvider.apiKey`），前端不用暴露 Key。
其它 `https://` 主机会被 403 拒绝（白名单 = 上表四家 ∪ `/api/proxy` 的允许列表），避免变成开放代理。

---

## 3. 三种复用方式

### ① 直接 fetch（任何框架 / 原生页面）

```js
const r = await fetch("https://gr.czyuma.com/api/aikit/models", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ provider: "ark", apiKey: myKey })
});
const j = await r.json();
if (j.ok) console.log("可用模型：", j.models.map(m => m.id));
else console.error("不可用：", j.error);
```

### ② 30 秒接入的小 SDK（推荐）

```html
<script src="https://gr.czyuma.com/ai-kit/ai-kit.js"></script>
<script>
  // 列表（顺带验证连通）
  AIKit.models({ provider: "ark", apiKey: myKey }).then(j => {
    if (!j.ok) return showError(j.error);
    render(j.models);                 // [{id,label}]
  });

  // 只测连通
  AIKit.test({ provider: "qwen", apiKey: "server" }).then(j => {
    console.log(j.ok ? "通" : "不通", j.error);
  });

  // 只想拿 id 数组
  AIKit.modelIds({ provider: "openai", apiKey: k }).then(ids => console.log(ids));

  // 有哪些预设服务商
  AIKit.providers().then(j => console.log(j.providers));
</script>
```

### ③ 现成面板（不改业务代码）

给页面加一行，右下角就多一个「🔌 API 体检」浮动面板（四家页签 / 测试连通 / 拉模型 / 勾选应用）：

```html
<script defer src="https://gr.czyuma.com/ai-pose/api-lab.js"></script>
```

面板读的是 `localStorage["pose_atlas_api_config_v5"]` 里的配置（ai-pose 自己的存储）。**别的项目想用，改 `readCfg()` 一处即可**，或者直接只用 ① ② 两种方式。

---

## 4. curl 示例

```bash
# 预设服务商
curl -s -X POST https://gr.czyuma.com/api/aikit/providers

# 用服务器代持的 Key 拉通义模型（无需自己给 Key）
curl -s -X POST https://gr.czyuma.com/api/aikit/models \
  -H 'Content-Type: application/json' \
  -d '{"provider":"qwen","apiKey":"server"}'

# 拿自己的方舟 Key 拉模型 / 测连通
curl -s -X POST https://gr.czyuma.com/api/aikit/test \
  -H 'Content-Type: application/json' \
  -d '{"provider":"ark","apiKey":"'"$ARK_KEY"'"}'
```

实测（2026-11-13）：`deepseek` 用真实 Key 返回 2 个模型（`deepseek-flash` = DeepSeek-V4.1-Flash、`deepseek-v4-pro` = DeepSeek-V4-Pro，含 1M 上下文与图片输入能力），伪造 Key 返回 `ok:false, auth:false, status:401`；`qwen`+`server` 返回 22 个模型（`qwen-image-3.0-pro`、`wan3.0-video`、`qwen3.8-max`、`deepseek-v4-pro`、`glm-5.3` 等）；`ark` 用伪造 Key 返回 `ok:false, auth:false, status:401` 并附方舟原文；`baseUrl` 指向白名单外主机返回 `403`。

---

## 5. 服务端在哪

- 实现：`/home/admin/.openclaw/workspace/server.py` 里的 `AIKIT_PROVIDERS`、`_handle_aikit(want:)`、`_aikit_*` 辅助函数，路由挂在 `do_POST` 的 `/api/aikit/providers|test|models`
- 本地源码副本：`D:\DSH\server-src\server.aikit.py`（生成脚本 `patch-aikit.py`，方法块 `aikit-block.py.txt`）
- 部署：`D:\DSH\server-src\deploy-aikit.sh`（会先备份到 `backups/server.py.pre-aikit-<时间戳>`，再 `py_compile` + 重启 `routing-server`）
- 前端 SDK 源文件：`D:\DSH\output\aipose\ai-kit.js`；面板源文件：`D:\DSH\output\aipose\api-lab.js`
