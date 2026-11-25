/*!
 * ai-kit.js —— AI 接口工具箱前端 SDK（10 行接入，任何页面可用）
 *
 *   <script src="https://gr.czyuma.com/ai-kit/ai-kit.js"></script>
 *   const list = await AIKit.models({ provider: "ark", apiKey: "你的 key" });
 *   const ping = await AIKit.test({ provider: "qwen", apiKey: "server" }); // 用服务器代持的 key
 *
 * 端点：POST {host}/api/aikit/{providers,models,test}
 *   provider: ark | openai | gemini | qwen  （也可只给 baseUrl 走别的兼容服务，主机需在服务端白名单）
 *   apiKey:   你的 key；传 "server" 表示用服务器代持的 key（目前仅 qwen）
 * 返回统一形状：
 *   models → { ok, auth, via:"models"|"probe", count, models:[{id,label}], tries:[{url,status,body}], error? }
 *   test   → 同上（test 在没有模型列表接口时用空 prompt 探针区分 401 与 400）
 */
(function (global) {
  "use strict";
  var BASE = (function () {
    var s = document.currentScript && document.currentScript.src;
    if (!s) return "";
    var m = s.match(/^(https?:\/\/[^/]+)/);
    return m ? m[1] : "";
  })();

  function post(path, body) {
    return fetch(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok && !j.error) j.error = "HTTP " + r.status;
        return j;
      });
    });
  }

  var AIKit = {
    base: BASE,
    /** 支持的服务商预设（id / label / baseUrl / listPaths / probePath / serverKey） */
    providers: function () { return post("/api/aikit/providers"); },
    /** 拉模型列表（也顺带完成连通性验证） */
    models: function (opt) {
      opt = opt || {};
      return post("/api/aikit/models", {
        provider: opt.provider, baseUrl: opt.baseUrl, apiKey: opt.apiKey, model: opt.model
      });
    },
    /** 只测连通：优先走模型列表，不行用探针（401=Key 无效，400=鉴权通过但参数/模型不对） */
    test: function (opt) {
      opt = opt || {};
      return post("/api/aikit/test", {
        provider: opt.provider, baseUrl: opt.baseUrl, apiKey: opt.apiKey, model: opt.model
      });
    },
    /** 便捷：拿纯字符串数组 */
    modelIds: function (opt) {
      return AIKit.models(opt).then(function (j) {
        return (j.models || []).map(function (m) { return m.id; });
      });
    }
  };

  global.AIKit = AIKit;
  if (typeof module === "object" && module.exports) module.exports = AIKit;
})(typeof window !== "undefined" ? window : globalThis);
