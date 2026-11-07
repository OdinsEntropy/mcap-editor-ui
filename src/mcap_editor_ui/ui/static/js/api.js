// JSON calls to the local server; errors become exceptions with the server's message.

async function call(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const q = (path) => encodeURIComponent(path ?? "");

export const api = {
  start: () => call("GET", "/api/start"),
  ls: (path) => call("GET", `/api/ls?path=${q(path)}`),
  info: (path) => call("GET", `/api/info?path=${q(path)}`),
  openStart: (path) => call("POST", "/api/open", { path }),
  openState: () => call("GET", "/api/open"),
  openCancel: () => call("POST", "/api/open/cancel", {}),
  output: (path, split) => call("GET", `/api/output?path=${q(path)}&split=${split ? 1 : 0}`),
  step: (path, topic, t, dir) => call("GET", `/api/step?path=${q(path)}&topic=${q(topic)}&t=${t}&dir=${dir}`),
  activity: (body) => call("POST", "/api/activity", body),
  settings: () => call("GET", "/api/settings"),
  saveSettings: (values) => call("POST", "/api/settings", { values }),
  estimate: (body) => call("POST", "/api/estimate", body),
  exportStart: (body) => call("POST", "/api/export", body),
  exportState: () => call("GET", "/api/export"),
  cancel: () => call("POST", "/api/cancel", {}),
};
