/* What the Studio asks, and of whom.
 *
 * Every call goes to the extension's own backend inside Superset — same origin, the person's Superset
 * session — which asks the SharpSight engine FOR THAT PERSON and applies the registry's rules to them
 * (registry/security/rbac.yaml). Nothing here knows the engine's address or holds a secret, and nothing here
 * decides anything: a refusal is the engine's, in the engine's words, and is shown as it came.
 */
const BASE = '/extensions/sharpsight/bi';
const slot = value => encodeURIComponent(value);
let csrf = null;

/* Superset refuses a write without its token (the extension's API sets csrf_exempt = False). The page is
 * given one by Flask; a panel of an extension would ask the host for it, and this is not one. */
async function token(el) {
  if (csrf) return csrf;
  if (el && el.dataset.csrf) return (csrf = el.dataset.csrf);
  try {
    csrf = await window.superset.authentication.getCSRFToken();
  } catch (error) {
    const answer = await fetch('/api/v1/security/csrf_token/', { credentials: 'same-origin' });
    csrf = (await answer.json()).result;
  }
  return csrf;
}

export function client(el) {
  async function ask(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRFToken'] = await token(el);
    }
    const answer = await fetch(BASE + path, {
      method, headers, credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let said = {};
    try { said = await answer.json(); } catch (error) { said = {}; }
    if (!answer.ok) {
      const problem = new Error(said.message || `refused (${answer.status})`);
      problem.status = answer.status;
      throw problem;
    }
    return said.result;
  }

  const project = el.dataset.project;
  return {
    studio: () => ask('GET', `/projects/${slot(project)}/studio`),
    save: (spec, had) => ask('PUT', `/projects/${slot(project)}/draft`, { spec, token: had }),
    drop: () => ask('DELETE', `/projects/${slot(project)}/draft`),
    check: () => ask('POST', `/projects/${slot(project)}/draft/validate`),
    commit: comment => ask('POST', `/projects/${slot(project)}/draft/commit`, { comment }),
    tiles: () => ask('POST', `/projects/${slot(project)}/pictures`),
    propose: request => ask('POST', `/projects/${slot(project)}/propose`, { request }),
    compose: wanted => ask('POST', `/projects/${slot(project)}/compose`, wanted),
    report: () => ask('GET', `/projects/${slot(project)}/report`),
    changes: () => ask('GET', `/projects/${slot(project)}/changes`),
    run: id => ask('GET', `/runs/${slot(id)}`),
    datasets: () => ask('GET', '/datasets'),
    metrics: () => ask('GET', '/metrics'),
    lineage: dataset => ask('GET', `/datasets/${slot(dataset)}/lineage`),
    picture: (slotId, taken) => `${BASE}/projects/${slot(project)}/picture/${slot(slotId)}?v=${slot(taken || '0')}`,
    // the immutable package a version was cut as. Reading it is not promoting it: a promotion is approved
    // by a second person, on the command line.
    packageUrl: version => `${BASE}/projects/${slot(project)}/versions/${slot(version)}/package`,
    project,
  };
}
