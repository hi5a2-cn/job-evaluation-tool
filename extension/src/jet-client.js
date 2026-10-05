export const DEFAULT_PORT = 47615;

/**
 * 构造本机 Jet 基础 URL。端口必须是 1–65535 整数。
 */
export function makeBaseUrl(port) {
  const p = Number(port);
  if (!Number.isInteger(p) || p < 1 || p > 65535) {
    throw new Error(`Invalid port: ${port}. Must be an integer between 1 and 65535.`);
  }
  return `http://127.0.0.1:${p}`;
}

/**
 * 断言 URL 必须以 http://127.0.0.1: 开头。
 */
export function assertJetUrl(url) {
  if (!url || typeof url !== "string" || !url.startsWith("http://127.0.0.1:")) {
    throw new Error(`Forbidden Jet URL: ${url}. Only http://127.0.0.1: is permitted.`);
  }
}

/**
 * 创建 Jet 本机客户端工厂函数（不在顶层访问 chrome，方便测试）。
 */
export function createJetClient({ getToken, getPort, fetchImpl = globalThis.fetch } = {}) {
  return {
    async call(method, path, body) {
      try {
        const port = getPort ? await getPort() : DEFAULT_PORT;
        const activePort = port || DEFAULT_PORT;
        const baseUrl = makeBaseUrl(activePort);
        const url = `${baseUrl}${path}`;
        assertJetUrl(url);

        const headers = {
          "Content-Type": "application/json",
        };

        const token = getToken ? await getToken() : null;
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
        }

        const options = {
          method,
          headers,
        };

        if (body !== undefined && body !== null) {
          options.body = JSON.stringify(body);
        }

        const res = await fetchImpl(url, options);

        let data = null;
        try {
          data = await res.json();
        } catch {
          // Response body is not JSON or empty
        }

        if (res.ok) {
          return {
            ok: true,
            status: res.status,
            data,
          };
        }

        const error = (data && data.error) ? data.error : "unknown";
        let viewState = "failed";

        if (res.status === 401) {
          viewState = "unpaired";
        } else if ((res.status === 404 || res.status === 409) && error === "no_profile") {
          viewState = "no_profile";
        } else if (res.status === 409 && error === "no_llm_key") {
          viewState = "no_llm_key";
        } else if (res.status === 403 && error === "consent_required") {
          viewState = "consent_required";
        } else if (res.status === 429 && error === "quota_exhausted") {
          viewState = "quota_exhausted";
        } else if (res.status === 400 && error === "hash_mismatch") {
          viewState = "hash_mismatch";
        } else if ((res.status === 502 || res.status === 504) && error === "llm_failed") {
          viewState = "llm_failed";
        } else if (res.status === 422) {
          if (error === "self_name_unavailable") {
            viewState = "self_name_unavailable";
          } else if (error === "all_suggestions_dropped") {
            viewState = "all_suggestions_dropped";
          } else {
            viewState = "unrecognized";
          }
        }

        return {
          ok: false,
          status: res.status,
          error,
          viewState,
          data,
        };
      } catch (err) {
        return {
          ok: false,
          status: 0,
          error: err?.message || String(err),
          viewState: "jet_down",
        };
      }
    },

    async preview(payload) {
      return this.call("POST", "/v1/chat/preview", payload);
    },

    async generate(payload) {
      return this.call("POST", "/v1/chat/generate", payload);
    },

    // 同意的字段版本由服务端决定（CONSENT_FIELDS_VERSION），请求不带版本号
    async consent() {
      return this.call("POST", "/v1/chat/consent", {});
    },

    async revokeConsent() {
      return this.call("POST", "/v1/chat/revoke-consent", {});
    },

    async consentStatus() {
      return this.call("GET", "/v1/chat/consent-status");
    },

    async getExperience() {
      return this.call("GET", "/v1/experience");
    },

    async putExperience(items) {
      return this.call("PUT", "/v1/experience", items);
    },

    async getResumes() {
      return this.call("GET", "/v1/resumes");
    },

    async uploadResume(payload) {
      return this.call("POST", "/v1/resumes/upload", payload);
    },

    async regenerateResume(slot, payload = {}) {
      return this.call("POST", `/v1/resumes/${slot}/regenerate`, payload);
    },

    async putResumes(items) {
      const payload = Array.isArray(items) ? { items } : items;
      return this.call("PUT", "/v1/resumes", payload);
    },

    async deleteResume(slot) {
      return this.call("DELETE", `/v1/resumes/${slot}`);
    },

    async getStrictIndustries() {
      return this.call("GET", "/v1/strict-industries");
    },

    async putStrictIndustries(selected) {
      return this.call("PUT", "/v1/strict-industries", { selected });
    },

    async postChatJob(payload) {
      return this.call("POST", "/v1/chat/job", payload);
    },

    async getLlmKey() {
      return this.call("GET", "/v1/llm-key");
    },

    async putLlmKey(apiKey) {
      return this.call("PUT", "/v1/llm-key", { api_key: apiKey });
    },

    async deleteLlmKey() {
      return this.call("DELETE", "/v1/llm-key");
    },

    async testLlmKey(apiKey = null) {
      const payload = apiKey ? { api_key: apiKey } : {};
      return this.call("POST", "/v1/llm-key/test", payload);
    },

    async prejudgeJobs(jobs) {
      return this.call("POST", "/v1/prejudge", { jobs });
    },

    async getPrejudgeSettings() {
      return this.call("GET", "/v1/prejudge/settings");
    },

    async putPrejudgeSettings(daily_prejudge_limit) {
      return this.call("PUT", "/v1/prejudge/settings", { daily_prejudge_limit });
    },
  };
}
