// SA3 UI kit: fetch wrappers for the studio's /sa3/* routes (same origin, spec 2.1).
// get/post throw an Error with the route's {"error"} message on a non-2xx answer.

async function answer(r) {
  let d = null;
  try { d = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) {
    throw new Error((d && d.error) || `${r.status} ${r.statusText}`);
  }
  return d;
}

export async function get(path, params = {}) {
  const q = new URLSearchParams(params).toString();
  return answer(await fetch(path + (q ? "?" + q : ""), { cache: "no-store" }));
}

export async function post(path, body = {}) {
  return answer(await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}

// a render, addressed by render id (its path under outputs/, library.render_id)
export const audioUrl = (id) => "/sa3/audio?id=" + encodeURIComponent(id);
export const specUrl = (id) => "/sa3/spec?id=" + encodeURIComponent(id);
export const setLabel = (id, fields) => post("/sa3/label", { id, ...fields });
export const setStar = (id, starred) => post("/sa3/star", { id, starred });
