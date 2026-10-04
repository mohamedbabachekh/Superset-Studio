// What the panel asks. Every call goes to the extension's own backend inside Superset (same origin, the
// person's Superset session), which asks the SharpSight engine for that person. Nothing here knows the engine's
// address or holds a secret.
const BASE = '/extensions/sharpsight/bi';

async function ask(method, path, body) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRFToken'] = await window.superset.authentication.getCSRFToken();
  }
  const answer = await fetch(BASE + path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let said = {};
  try {
    said = await answer.json();
  } catch (error) {
    said = {};
  }
  if (!answer.ok) {
    throw new Error(said.message || `refused (${answer.status})`);
  }
  return said.result;
}

export const projects = () => ask('GET', '/projects');
export const validate = project => ask('GET', `/projects/${encodeURIComponent(project)}/changes`);
export const commit = (project, comment) => ask('POST', `/projects/${encodeURIComponent(project)}/commit`, { comment });
export const run = id => ask('GET', `/runs/${encodeURIComponent(id)}`);
