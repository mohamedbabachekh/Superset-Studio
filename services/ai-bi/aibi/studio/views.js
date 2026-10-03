'use strict';
/* Every screen of the navigation except the builder: Develop, Govern, Deliver, Operate, Clients.
   Each is a view over what the service already knows — projects, versions, runs, validation reports, the
   asset catalogue, the audit trail. Where this kit has nothing behind an entry, the screen says so. */
(() => {
  const { S, h, enc, fmt, api, toast, badge, dot, table, field, select, dialog, impactBox, go, page, download, blob } = Studio;
  const V = Studio.views;
  const TYPE_NAMES = { dataset: 'Datasets', dimension: 'Dimensions', metric: 'Metrics', chart: 'Charts', section: 'Sections',
                       dashboard: 'Dashboards', template: 'Dashboard templates', chart_template: 'Chart templates' };
  const when = (iso) => (iso ? iso.replace('T', ' ').replace(/\+00:00$/, ' UTC') : '—');
  const kv = (pairs) => h('div', { class: 'kv small' }, pairs.filter(Boolean).flatMap(([k, v]) =>
    [h('span', { text: k }), v instanceof Node ? v : h('span', { text: v === null || v === undefined || v === '' ? '—' : String(v) })]));
  Studio.kv = kv;

  async function everything() {
    const projects = await api('GET', '/projects');
    const details = await Promise.all(projects.map((p) => api('GET', `/projects/${enc(p.project_id)}`).catch(() => null)));
    return projects.map((p, i) => ({ ...p, versions: details[i] ? details[i].versions : [], git: details[i] ? details[i].git : [] }));
  }
  async function validations() {
    const projects = (await api('GET', '/projects')).filter((p) => p.version);
    const reports = await Promise.all(projects.map((p) => api('GET', `/projects/${enc(p.project_id)}/versions/${p.version}/validation`).catch(() => null)));
    return projects.map((p, i) => ({ project: p, report: reports[i] })).filter((x) => x.report);
  }

  /* A run, step by step — used by Pipelines and by the builder's « Build ». */
  Studio.runSteps = (run) => {
    const mark = { done: ['✓', 'var(--success)'], skipped: ['–', 'var(--muted)'], failed: ['✗', 'var(--error)'], running: ['…', 'var(--info)'], pending: ['', 'var(--muted)'] };
    return h('div', { class: 'stack' },
      h('div', { class: 'row wrap' }, badge(run.status), run.quality_gate ? badge(run.quality_gate) : null,
        h('span', { class: 'muted small', text: `run ${run.id} · ${run.user} · ${when(run.started_at)}` })),
      run.error ? h('div', { class: 'note bad small', text: run.error }) : null,
      h('div', { class: 'steps' }, run.steps.map((step) => h('div', { class: 'step' },
        h('b', { style: `color:${mark[step.status][1]}`, text: mark[step.status][0] }), h('span', { class: 'muted', text: step.number }),
        h('div', null, h('span', { style: step.status === 'pending' ? 'color:var(--muted)' : '', text: step.title }),
          step.status === 'skipped' ? h('span', { class: 'muted small', text: `  ${step.summary.reason || ''}` }) : null,
          step.summary && step.summary.checks !== undefined ? h('span', { class: 'muted small', text: `  ${step.summary.checks} checks, ${step.summary.failures} failed, ${step.summary.warnings} warnings` }) : null,
          step.error ? h('div', { class: 'small', style: 'color:var(--error)', text: step.error }) : null)))));
  };

  // ── Home ───────────────────────────────────────────────────────────────────────────────────────────────
  V.home = async () => {
    const [projects, tree, health, runs] = await Promise.all([api('GET', '/projects'), api('GET', '/assets/tree'),
      fetch('/health').then((r) => r.json()), api('GET', '/runs')]);
    const gates = {};
    projects.forEach((p) => { gates[p.status] = (gates[p.status] || 0) + 1; });
    const stat = (value, label, view) => h('div', { class: 'card click', onclick: () => go(view) }, h('div', { class: 'stat', text: value }), h('div', { class: 'muted', text: label }));
    return page('BI Studio', 'A development environment for BI: assets defined once and reused, dashboards composed of sections, every change validated, versioned and reviewed before it is deployed.',
      h('div', { class: 'row', style: 'margin-bottom:14px' },
        h('button', { class: 'btn primary', text: 'New dashboard', disabled: !Studio.can('bi.generate'), onclick: () => V.newDashboard() }),
        h('button', { class: 'btn', text: 'Ask the AI for one', disabled: !Studio.can('bi.generate'), onclick: () => V.askAi() })),
      h('div', { class: 'cards' },
        stat(projects.length, 'BI projects', 'projects'), stat(tree.counts.dataset, 'datasets', 'data'), stat(tree.counts.metric, 'metrics', 'metrics'),
        stat(tree.counts.chart + tree.counts.section, 'charts and sections in the library', 'data'), stat(tree.counts.template, 'dashboard templates', 'templates')),
      h('h2', { text: 'Where the projects stand', style: 'margin:18px 0 8px' }),
      h('div', { class: 'row wrap' }, Object.keys(gates).length ? Object.entries(gates).map(([gate, n]) => h('span', { class: 'row' }, badge(gate), h('b', { text: n })))
        : h('span', { class: 'muted', text: 'No project yet.' })),
      h('h2', { text: 'The service', style: 'margin:18px 0 8px' }),
      h('div', { class: 'card' }, kv([['Status', badge(health.status === 'ok' ? 'pass' : 'fail', health.status)], ['BI engine (Superset)', health.bi_engine ? 'reachable' : 'NOT reachable'],
        ['Warehouse', health.warehouse ? 'reachable' : 'NOT reachable'], ['Language model', health.ai_model],
        ['Untested versions', health.untested_components.length ? health.untested_components.join('; ') : 'none: everything running is a tested version']])),
      h('h2', { text: 'Latest runs', style: 'margin:18px 0 8px' }),
      table([{ title: 'RUN', cell: (r) => h('span', { class: 'mono', text: r.id }) }, { title: 'PROJECT', cell: (r) => r.project_id || '—' },
             { title: 'RESULT', cell: (r) => badge(r.status) }, { title: 'GATE', cell: (r) => (r.quality_gate ? badge(r.quality_gate) : '—') },
             { title: 'BY', cell: (r) => r.user }, { title: 'STARTED', cell: (r) => when(r.started_at) }], runs.slice(0, 6), (r) => go('pipelines', r.id)));
  };

  // ── Develop ────────────────────────────────────────────────────────────────────────────────────────────
  V.newDashboard = async () => {
    const templates = await api('GET', '/templates');
    const title = h('input', { class: 'input', placeholder: 'CEM Regional Quality' });
    let template = templates[0].id;
    dialog('New dashboard', h('div', { class: 'stack' }, field('Subject — it names the BI project', title),
      field('Template', select(template, templates.map((t) => [t.id, `${t.name} — ${t.description}`]), (v) => { template = v; })),
      h('div', { class: 'small muted', text: 'It starts as a draft: an empty specification on the template, every section named. Nothing is created in Superset until you build it.' })),
      [['Create', 'primary', async (close) => {
        try { const doc = await api('POST', '/builder/projects', { title: title.value.trim(), template }); close(); go('builder', doc.project_id); }
        catch (error) { toast(error.message, 'bad'); } }]]);
  };
  V.askAi = () => {
    const prompt = h('textarea', { class: 'input', style: 'min-height:150px' },
      'Create a CEM Regional Quality dashboard using the Management template.\n\nUse QoE Score, Active Users and Degraded Users.\n\nShow weekly trend, share by RAT, regional ranking and detail by region.\n\nAdd Date and Region filters.');
    dialog('Ask the AI for a dashboard', h('div', { class: 'stack' }, field('The request', prompt),
      h('div', { class: 'small muted', text: `Read by: ${S.config.ai_model}. It finds governed datasets and metrics, writes the specification, builds, validates and versions it — 22 steps, followed under Pipelines.` })),
      [['Generate', 'primary', async (close) => {
        try { const run = await api('POST', '/projects/generate', { prompt: prompt.value }); close(); go('pipelines', run.run_id); }
        catch (error) { toast(error.message, 'bad'); } }]]);
  };
  async function projectTable(title, lead) {
    const projects = await api('GET', '/projects');
    return page(title, lead,
      h('div', { class: 'row', style: 'margin-bottom:12px' },
        h('button', { class: 'btn primary', text: 'New dashboard', disabled: !Studio.can('bi.generate'), onclick: () => V.newDashboard() }),
        h('button', { class: 'btn', text: 'Ask the AI for one', disabled: !Studio.can('bi.generate'), onclick: () => V.askAi() })),
      table([{ title: 'PROJECT', cell: (p) => h('div', null, h('b', { text: p.title || p.project_id }), h('div', { class: 'mono muted', text: p.project_id })) },
             { title: 'TEMPLATE', cell: (p) => p.template || '—' }, { title: 'VERSION', cell: (p) => p.version || '—' },
             { title: 'GATE', cell: (p) => badge(p.status) }, { title: 'ENVIRONMENT', cell: (p) => p.environment },
             { title: 'LAST GENERATION', cell: (p) => (p.last_attempt ? h('span', { class: 'small', title: p.last_attempt.error || '', text: `${p.last_attempt.status}${p.last_attempt.error ? ' — ' + p.last_attempt.error.slice(0, 70) + '…' : ''}` }) : '—') },
             { title: 'BY', cell: (p) => p.created_by },
             { title: '', cell: (p) => (p.dashboard ? h('a', { href: p.dashboard, target: '_blank', rel: 'noopener', text: 'Superset ↗', onclick: (e) => e.stopPropagation() }) : '') }],
        projects, (p) => go('builder', p.project_id)));
  }
  V.projects = () => projectTable('Projects', 'A BI project is the unit that is developed, validated, versioned and deployed. Open one to compose its dashboard.');
  V.dashboards = () => projectTable('Dashboards', 'Dashboards are compositions: sections, and in them charts that reference shared assets. Open one in the builder.');

  function assetCard(found) {
    return h('div', { class: 'card click stack', onclick: () => go('asset', found.kind, found.id) },
      h('div', { class: 'row' }, dot(found.status), h('b', { class: 'grow ellipsis', text: found.name }), badge(found.status)),
      h('div', { class: 'mono muted ellipsis', text: found.id }),
      h('div', { class: 'small muted', style: 'min-height:34px', title: found.description || '', text: (found.description || '').length > 132 ? `${found.description.slice(0, 130).replace(/\s+\S*$/, '')}…` : found.description || '' }),
      h('div', { class: 'row small wrap' }, found.owner ? h('span', { text: `Owner: ${found.owner}` }) : null,
        found.facts.refresh ? h('span', { text: `Refresh: ${found.facts.refresh}` }) : null,
        found.facts.type && found.kind === 'chart' ? h('span', { text: found.facts.type }) : null,
        h('span', { class: 'grow' }), h('span', { class: 'muted', text: `Used by ${found.used_by.dashboards} dashboards · ${found.used_by.charts} charts` })));
  }
  async function assetPage(title, lead, kinds) {
    const cards = (await api('GET', '/assets')).filter((c) => kinds.includes(c.kind));
    const body = h('div', { class: 'stack' });
    let current = kinds[0];
    const tabs = h('div', { class: 'tabs', style: 'margin-bottom:12px' });
    const fill = () => {
      tabs.replaceChildren(...kinds.map((k) => h('button', { class: `tab ${k === current ? 'on' : ''}`, text: `${TYPE_NAMES[k]} (${cards.filter((c) => c.kind === k).length})`, onclick: () => { current = k; fill(); } })));
      const shown = cards.filter((c) => c.kind === current);
      const domains = [...new Set(shown.map((c) => c.domain_name))];
      body.replaceChildren(...domains.map((d) => h('div', { class: 'stack' }, h('h3', { class: 'muted', text: d }), h('div', { class: 'cards' }, shown.filter((c) => c.domain_name === d).map(assetCard)))),
        ...(shown.length ? [] : [h('div', { class: 'muted', text: current === 'chart' || current === 'section' ? 'None shared yet. In the builder, select a chart and « Share this chart ».' : 'None.' })]));
    };
    fill();
    return page(title, lead, kinds.length > 1 ? tabs : null, body);
  }
  V.data = () => assetPage('Data Assets', 'One library, filed under logical domains — not database schemas. A dataset used by twelve dashboards is one dataset, referenced twelve times.', ['dataset', 'dimension', 'chart', 'section']);
  V.metrics = () => assetPage('Metrics', 'The governed metrics. A dashboard does not define a KPI: it references one of these.', ['metric']);
  V.templates = () => assetPage('Templates', 'Dashboard templates fix where things go. Chart templates are how a chart is started without starting from scratch.', ['template', 'chart_template']);

  V.asset = async (kind, id) => {
    const found = await api('GET', `/assets/${kind}/${enc(id)}`);
    const d = found.definition || {};
    const facts = Object.entries(found.facts || {}).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length));
    const from = location.hash;
    const act = async (fn, done) => { try { await fn(); toast(done, 'ok'); Studio.refresh(from); } catch (error) {
      if (error.body && error.body.impact) dialog('This asset is in use', impactBox(error.body.impact), []); else toast(error.message, 'bad'); } };
    const library = ['chart', 'section'].includes(kind) && found.scope === 'shared';
    return page(found.name, found.description,
      h('div', { class: 'row wrap', style: 'margin-bottom:12px' }, badge(found.status), badge(found.scope), h('span', { class: 'badge', text: TYPE_NAMES[kind] }),
        found.domain_name ? h('span', { class: 'badge', text: found.domain_name }) : null, h('span', { class: 'mono muted', text: found.id }), h('span', { class: 'grow' }),
        h('button', { class: 'btn', text: found.favorite ? '★ Favorite' : '☆ Favorite', onclick: () => act(() => api(found.favorite ? 'DELETE' : 'PUT', `/assets/${kind}/${enc(id)}/favorite`), 'Done.') }),
        library && found.status !== 'certified' ? h('button', { class: 'btn', text: 'Certify', disabled: !Studio.can('assets.certify'), title: 'By someone other than its author', onclick: () => act(() => api('POST', `/assets/${kind}/${enc(id)}/certify`), 'Certified.') }) : null,
        library ? h('button', { class: 'btn danger', text: 'Delete', disabled: !Studio.can('assets.write'), onclick: () => act(async () => { await api('DELETE', `/assets/${kind}/${enc(id)}`); go('data'); }, 'Deleted.') }) : null),
      h('div', { style: 'display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:14px;align-items:start' },
        h('div', { class: 'stack' },
          h('div', { class: 'card' }, kv([found.owner ? ['Owner', found.owner] : null, ...facts.map(([k, v]) => [k.replace(/_/g, ' '), Array.isArray(v) ? v.join(', ') : typeof v === 'number' ? fmt(v) : v])])),
          kind === 'metric' ? h('div', { class: 'card stack' }, h('h3', { text: 'Definition' }), h('pre', { class: 'code mono', text: d.expression }),
            kv([['Lineage', `${d.lineage.datasets.join(', ')} · columns ${d.lineage.columns.join(', ')}`], ['Version', d.version], ['Classification', d.classification]])) : null,
          kind === 'dataset' ? h('div', { class: 'stack' }, h('h3', { text: 'Columns' }),
            table([{ title: 'COLUMN', cell: (r) => r[0] }, { title: 'LABEL', cell: (r) => r[1].label }, { title: 'TYPE', cell: (r) => r[1].type }, { title: 'ROLE', cell: (r) => r[1].role || '—' },
                   { title: 'CLASSIFICATION', cell: (r) => badge(r[1].readable ? 'pass' : 'fail', r[1].classification) }, { title: 'DESCRIPTION', cell: (r) => r[1].description || '' }], Object.entries(d.columns))) : null,
          ['chart', 'section'].includes(kind) && library ? h('div', { class: 'card' }, kv([['Revision', d.revision], ['Created by', d.created_by], ['Certified by', d.certified_by], ['Changed', when(d.updated_at)],
            kind === 'section' ? ['Charts, in order', d.charts.join(', ')]
              : ['Options', Object.entries(d.options).filter(([, v]) => v !== null).map(([k, v]) => `${k.replace('_', ' ')}: ${v}`).join(' · ') || 'the defaults']])) : null,
          kind === 'template' ? h('div', { class: 'card stack' }, h('h3', { text: 'Sections and slots' }),
            (d.sections || []).map((s) => h('div', { class: 'small' }, h('b', { text: s.title || s.id }), `  rows ${s.rows.join(', ')}`)),
            table([{ title: 'SLOT', cell: (r) => r[0] }, { title: 'FOR', cell: (r) => r[1].purpose }, { title: 'ACCEPTS', cell: (r) => r[1].accepts.join(', ') }, { title: 'MANDATORY', cell: (r) => (r[1].mandatory ? 'yes' : '') }], Object.entries(d.slots))) : null),
        h('div', { class: 'stack' }, h('h3', { text: 'Where is this used?' }),
          found.impact ? impactBox(found.impact) : h('div', { class: 'note', text: 'Local to its dashboard: used there, and nowhere else.' }),
          found.impact && found.impact.used_by.dashboards.length ? table([{ title: 'DASHBOARD', cell: (r) => r.title }, { title: 'GATE', cell: (r) => badge(r.status) }, { title: 'ENV', cell: (r) => r.environment }],
            found.impact.used_by.dashboards, (r) => go('builder', r.id)) : null,
          found.impact && found.impact.used_by.charts.length ? table([{ title: 'CHART', cell: (r) => r.title }, { title: 'IN', cell: (r) => r.dashboard }, { title: 'SLOT', cell: (r) => r.slot }], found.impact.used_by.charts) : null)));
  };

  V.datalab = async () => {
    const datasets = await api('GET', '/datasets');
    let current = datasets.length ? datasets[0].id : '';
    const out = h('div', { class: 'stack' });
    const sql = h('textarea', { class: 'input mono', style: 'min-height:110px' }, 'select region, sum(active_users) as active_users\nfrom bi.mart_cem_qoe_daily\ngroup by region\norder by 2 desc');
    const show = (rows) => out.replaceChildren(h('div', { class: 'small muted', text: `${rows.row_count ?? rows.rows.length} rows${rows.truncated ? ' (capped)' : ''}${rows.duration_ms !== undefined ? ` · ${rows.duration_ms} ms` : ''}` }),
      table(rows.columns.map((name, i) => ({ title: name.toUpperCase(), cell: (r) => String(r[i] ?? '') })), rows.rows.slice(0, 200)));
    const run = async (fn) => { try { await fn(); } catch (error) { out.replaceChildren(h('div', { class: 'note bad', text: error.message })); } };
    return page('SQL / Data Lab', 'The governed road to the data: one SELECT over registered datasets you may read, through the read-only gateway, row-capped and audited. Free exploration stays in Superset’s SQL Lab — it is not rebuilt here.',
      h('div', { class: 'card stack' }, h('h3', { text: 'Preview a dataset' }),
        h('div', { class: 'row' }, h('div', { style: 'width:340px' }, select(current, datasets.map((d) => [d.id, `${d.name} — ${d.id}`]), (v) => { current = v; })),
          h('button', { class: 'btn', text: 'Preview rows', onclick: () => run(async () => show(await api('POST', '/tools/get_dataset_sample', { arguments: { dataset_id: current, limit: 20 } }))) }))),
      h('div', { class: 'card stack', style: 'margin-top:12px' }, h('h3', { text: 'A query' }), sql,
        h('div', { class: 'row' },
          h('button', { class: 'btn', text: 'Check', title: 'Would it be allowed? Runs nothing.', onclick: () => run(async () => {
            const verdict = await api('POST', '/tools/validate_sql', { arguments: { sql: sql.value } });
            out.replaceChildren(h('div', { class: `note ${verdict.ok ? '' : 'bad'}`, text: verdict.ok ? `Allowed. It reads ${verdict.datasets.join(', ')}.` : `Refused: ${verdict.problems.join('; ')}` })); }) }),
          h('button', { class: 'btn primary', text: 'Run', onclick: () => run(async () => show(await api('POST', '/tools/execute_readonly_sql', { arguments: { sql: sql.value, limit: 200, purpose: 'BI Studio data lab' } }))) }),
          h('span', { class: 'grow' }), h('a', { href: Studio.supersetUrl('/sqllab/'), target: '_blank', rel: 'noopener', text: 'Open SQL Lab in Superset ↗' }))),
      h('div', { style: 'margin-top:12px' }, out),
      h('h2', { text: 'Superset’s SQL Lab', style: 'margin:18px 0 4px' }),
      h('div', { class: 'small muted', text: 'Superset itself, framed. It is open to the Superset accounts that hold it — an administrator in this kit; the roles a dashboard is granted to are refused it, on purpose.' }),
      h('div', { class: 'framebox', style: 'height:560px;margin:8px 0 0' }, h('iframe', { class: 'embedded', title: 'Superset SQL Lab', loading: 'lazy', src: Studio.supersetUrl('/sqllab/') })));
  };

  /* dbt, in the same place: what SharpSight keeps of its models and tests, and — framed — dbt's own
     documentation site, with its lineage graph, as the production build published it. */
  V.models = async () => {
    const found = await api('GET', '/dbt/models');
    const tests = (m) => (m.tests.failed.length ? badge('fail', `${m.tests.failed.length} failed`) : m.tests.total
      ? badge(m.tests.not_run ? 'skipped' : 'pass', m.tests.not_run ? `${m.tests.passed} of ${m.tests.total} run` : `${m.tests.passed} passed`) : h('span', { class: 'muted', text: '—' }));
    const columns = [
      { title: 'MODEL', cell: (m) => h('div', null, h('b', { text: m.name }), h('div', { class: 'mono muted', text: m.path })) },
      { title: 'LAYER', cell: (m) => badge(m.layer === 'generated' ? 'generated' : '', m.layer) },
      { title: 'BUILT AS', cell: (m) => h('span', { class: 'mono', text: `${m.relation || '—'}${m.materialized ? ' · ' + m.materialized : ''}` }) },
      { title: 'TESTS', cell: tests },
      { title: 'BUILT FROM', cell: (m) => h('span', { class: 'small', text: m.built_from.join(', ') || '—' }) },
      { title: 'GOVERNED DATASET', cell: (m) => (m.dataset ? h('a', { text: `${m.dataset.name}${m.dataset.certified ? '' : ' (not certified)'}`, onclick: () => go('asset', 'dataset', m.dataset.id) }) : h('span', { class: 'muted', text: '—' })) }];
    const governed = found.models.filter((m) => m.environment === 'prod'), generated = found.models.filter((m) => m.environment !== 'prod');
    return h('div', { class: 'embed' },
      h('div', { class: 'intro stack' }, h('h1', { text: 'Data Models (dbt)' }),
        h('p', { class: 'lead', style: 'margin:0', text: `dbt builds the data; SharpSight keeps what dbt says about it. The governed project stands at commit ${found.governed_commit}; a model the engine generates lives on a branch of its own and is built in a development schema until a person merges it.` }),
        table(columns, governed),
        generated.length ? [h('h3', { text: 'Generated by the engine — on a branch, not merged' }), table(columns, generated)] : null),
      found.docs ? h('div', { class: 'framebox' }, h('iframe', { class: 'embedded', title: 'dbt docs', src: found.docs }))
        : h('div', { class: 'note warn', style: 'margin:0 22px', text: 'dbt’s documentation site has not been published yet: it is written by the production build (the dbt-runner), at the next start.' }));
  };

  // ── Govern ─────────────────────────────────────────────────────────────────────────────────────────────
  async function checksPage(title, lead, category) {
    const found = await validations();
    return page(title, lead, found.length ? found.map(({ project, report }) => {
      const checks = report.checks.filter((c) => !category || c.category === category);
      const by = report.summary.by_category;
      return h('div', { class: 'stack', style: 'margin-bottom:18px' },
        h('div', { class: 'row wrap' }, h('h2', { text: `${project.title || project.project_id} · v${project.version}` }), badge(project.status),
          category ? null : Object.entries(by).map(([name, n]) => h('span', { class: 'badge', text: `${name}: ${n.pass} ✓ ${n.warn ? n.warn + ' ⚠ ' : ''}${n.fail ? n.fail + ' ✗' : ''}` }))),
        table([{ title: '', cell: (c) => badge(c.status) }, ...(category ? [] : [{ title: 'CATEGORY', cell: (c) => c.category }]), { title: 'CHECK', cell: (c) => c.name },
               { title: 'ON', cell: (c) => c.subject }, { title: 'FOUND', cell: (c) => c.detail }], category ? checks : checks.filter((c) => c.status !== 'pass').concat(checks.filter((c) => c.status === 'pass').slice(0, 12))),
        category ? null : h('div', { class: 'small muted', text: `${checks.length} checks in all; the failed and the warned are shown first, then the first twelve that passed.` }));
    }) : h('div', { class: 'note', text: 'No version has been built yet: checks are measured when a dashboard is built.' }));
  }
  V.tests = () => checksPage('Tests', 'Every check of the last build of each project — data, SQL, BI, security, performance, visual. A check is a measurement, recorded whether it passed or not.');
  V.dataquality = () => checksPage('Data Quality', 'The data checks of each project’s last build: schema, null rate, duplicates, row count, freshness, ranges, expected values, metric validity.', 'data');
  V.performance = () => checksPage('Performance', 'Measured at each build, against registry/policies.yaml: query duration, dashboard load, query count, result size, and four people at once.', 'performance');

  V.certification = async () => {
    const cards = (await api('GET', '/assets')).filter((c) => ['dataset', 'metric', 'chart', 'section'].includes(c.kind) && c.scope === 'shared');
    return page('Certification', 'What is certified, and what is only registered. Datasets and metrics are certified in the governed registries; a shared chart or section is certified here, by someone other than its author.',
      table([{ title: 'ASSET', cell: (c) => h('b', { text: c.name }) }, { title: 'KIND', cell: (c) => TYPE_NAMES[c.kind] }, { title: 'DOMAIN', cell: (c) => c.domain_name },
             { title: 'OWNER', cell: (c) => c.owner || '—' }, { title: 'STATUS', cell: (c) => badge(c.status) }, { title: 'USED BY', cell: (c) => `${c.used_by.dashboards} dashboards` }],
        cards.sort((a, b) => (a.status === 'certified') - (b.status === 'certified')), (c) => go('asset', c.kind, c.id)));
  };

  V.review = async () => {
    const all = await everything();
    const waiting = all.filter((p) => ['READY_FOR_REVIEW', 'UAT'].includes(p.status));
    const from = location.hash;
    let deciding = false;                 // one decision at a time: a second click while the first is checked is not a second approval
    const decide = async (project, path, body) => {
      if (deciding) return;
      deciding = true;
      toast('Checking what is in Superset against what was validated…');
      try { await api('POST', `/projects/${enc(project)}/${path}`, body); toast('Recorded.', 'ok'); Studio.refresh(from); }
      catch (error) { toast(error.message, 'bad'); }
      deciding = false;
    };
    return page('Code Review', 'What is waiting for a person. UAT needs a reviewer; PROD needs a second person, an explicit confirmation and a comment. The requester cannot approve their own dashboard for production.',
      waiting.length ? waiting.map((p) => {
        const version = p.versions[p.versions.length - 1];
        const target = p.status === 'READY_FOR_REVIEW' ? 'UAT' : 'PROD';
        const comment = h('input', { class: 'input', placeholder: target === 'PROD' ? 'What was checked in UAT (required)' : 'A comment for the record' });
        const shot = h('img', { class: 'shot', style: 'max-height:460px', alt: 'The dashboard, as rendered when it was validated' });
        blob(`/projects/${enc(p.project_id)}/versions/${p.version}/screenshot`).then((url) => { shot.src = url; }).catch(() => shot.remove());
        return h('div', { class: 'card stack', style: 'margin-bottom:14px' },
          h('div', { class: 'row wrap' }, h('h2', { text: `${p.title || p.project_id} · v${p.version}` }), badge(p.status), h('span', { class: 'muted', text: `requested by ${version.created_by}` }), h('span', { class: 'grow' }),
            h('a', { text: 'Open in the builder', onclick: () => go('builder', p.project_id) }), p.dashboard ? h('a', { href: p.dashboard, target: '_blank', rel: 'noopener', text: 'Superset ↗' }) : null),
          h('div', { style: 'display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px' }, shot,
            h('div', { class: 'stack' }, kv([['Gates', version.history.map((x) => x.status).join(' → ')], ['Validation', `${version.validation.checks} checks, ${version.validation.failures} failed, ${version.validation.warnings} warnings`],
              ['Git commit', h('span', { class: 'mono', text: (version.git_commit || '').slice(0, 12) })], ['Template', version.template_version],
              ['Package', version.package.file ? h('a', { text: `${version.package.file} · sha256 ${version.package.sha256.slice(0, 16)}…`, onclick: () => download(`/projects/${enc(p.project_id)}/versions/${p.version}/package`, version.package.file) }) : '—']]),
              comment, h('div', { class: 'row' },
                h('button', { class: 'btn primary', text: `Approve for ${target}`, disabled: !Studio.can(target === 'UAT' ? 'bi.approve_uat' : 'bi.approve_prod'),
                  onclick: () => decide(p.project_id, 'approve', { target, comment: comment.value, confirm: target === 'PROD' }) }),
                h('button', { class: 'btn danger', text: 'Reject', disabled: !Studio.can('bi.reject'), onclick: () => decide(p.project_id, 'reject', { comment: comment.value || 'rejected' }) })))));
      }) : h('div', { class: 'note', text: 'Nothing is waiting for review.' }));
  };

  V.impact = async (kind, id) => {
    const cards = (await api('GET', '/assets')).filter((c) => c.scope === 'shared' && c.kind !== 'dashboard');
    const out = h('div', { class: 'stack' });
    const show = async (k, i) => { try { const found = await api('GET', `/assets/${k}/${enc(i)}/impact`);
      out.replaceChildren(h('h2', { text: `Changing « ${(cards.find((c) => c.kind === k && c.id === i) || { name: i }).name} »` }), impactBox(found),
        h('a', { text: 'Open the asset →', onclick: () => go('asset', k, i) })); } catch (error) { out.replaceChildren(h('div', { class: 'note bad', text: error.message })); } };
    if (kind && id) show(kind, id);
    return page('Impact Analysis', 'Before an asset is changed: what uses it, and whether any of that has been handed on to UAT or PROD.',
      h('div', { style: 'width:460px;margin-bottom:14px' }, select(kind && id ? `${kind}|${id}` : '', [['', 'Choose an asset…'], ...cards.map((c) => [`${c.kind}|${c.id}`, `${TYPE_NAMES[c.kind]} · ${c.name}`])],
        (v) => { if (v) { const [k, i] = v.split('|'); show(k, i); } })), out);
  };

  V.catalog = async (query) => {
    const state = { q: query || '', kind: '', domain: '', status: '', owner: '', used_by: '' };
    const [all, projects] = await Promise.all([api('GET', '/assets'), api('GET', '/projects')]);
    const out = h('div', { class: 'stack' });
    const run = async () => {
      const params = Object.entries(state).filter(([, v]) => v).map(([k, v]) => `${k}=${enc(v)}`).join('&');
      const found = await api('GET', `/assets/search?${params}`);
      out.replaceChildren(h('div', { class: 'muted', text: `${found.total} result(s)` }), ...Object.entries(found.groups).map(([k, cards]) => h('div', { class: 'stack' },
        h('h3', { class: 'muted', text: TYPE_NAMES[k].toUpperCase() }),
        table([{ title: 'NAME', cell: (c) => h('span', { class: 'row' }, dot(c.status), h('b', { text: c.name })) }, { title: 'DOMAIN', cell: (c) => c.domain_name },
               { title: 'STATUS', cell: (c) => badge(c.status) }, { title: 'OWNER', cell: (c) => c.owner || '—' }, { title: 'USED BY', cell: (c) => `${c.used_by.dashboards} dashboards · ${c.used_by.charts} charts` }],
          cards, (c) => (c.kind === 'dashboard' ? go('builder', c.id) : go('asset', c.kind, c.id))))));
    };
    const pick = (key, options) => select(state[key], options, (v) => { state[key] = v; run(); });
    await run();
    return page('Data Catalog', 'Search before navigation: every kind of asset at once.',
      h('div', { class: 'row wrap', style: 'margin-bottom:14px;align-items:flex-end' },
        h('div', { style: 'width:280px' }, field('Search', h('input', { class: 'input', value: state.q, placeholder: 'QoE', oninput: (e) => { state.q = e.target.value; run(); } }))),
        h('div', { style: 'width:150px' }, field('Type', pick('kind', [['', 'All'], ...Object.entries(TYPE_NAMES)]))),
        h('div', { style: 'width:170px' }, field('Domain', pick('domain', [['', 'All'], ...[...new Map(all.filter((c) => c.domain).map((c) => [c.domain, c.domain_name])).entries()]]))),
        h('div', { style: 'width:150px' }, field('Status', pick('status', [['', 'All'], ['certified', 'Certified'], ['not_certified', 'Not certified'], ['generated', 'Generated'], ['local', 'Local']]))),
        h('div', { style: 'width:130px' }, field('Owner', h('input', { class: 'input', oninput: (e) => { state.owner = e.target.value; run(); } }))),
        h('div', { style: 'width:200px' }, field('Used by', pick('used_by', [['', 'Any dashboard'], ...projects.map((p) => [p.project_id, p.title || p.project_id])])))), out);
  };

  // ── Deliver ────────────────────────────────────────────────────────────────────────────────────────────
  V.releases = async () => {
    const all = await everything();
    const rows = all.flatMap((p) => p.versions.map((v) => ({ p, v }))).reverse();
    return page('Releases', 'Every version: what it is made of, the gates it went through, and its .ssbi package — cut once, when it became reviewable.',
      table([{ title: 'PROJECT', cell: (r) => h('b', { text: r.p.title || r.p.project_id }) }, { title: 'VERSION', cell: (r) => r.v.version }, { title: 'GATE', cell: (r) => badge(r.v.status) },
             { title: 'THE WAY THERE', cell: (r) => h('span', { class: 'small', text: r.v.history.map((x) => x.status).join(' → ') }) },
             { title: 'COMMIT', cell: (r) => h('span', { class: 'mono', text: (r.v.git_commit || '').slice(0, 10) }) }, { title: 'TEMPLATE', cell: (r) => r.v.template_version },
             { title: 'PACKAGE', cell: (r) => (r.v.package.file ? h('a', { text: r.v.package.file, title: `sha256 ${r.v.package.sha256}`, onclick: () => download(`/projects/${enc(r.p.project_id)}/versions/${r.v.version}/package`, r.v.package.file) }) : '—') },
             { title: 'CREATED', cell: (r) => when(r.v.created_at) }], rows));
  };
  V.pipelines = async (runId) => {
    if (runId) {
      const holder = h('div');
      const follow = async () => { const run = await api('GET', `/runs/${runId}`); holder.replaceChildren(h('div', { class: 'card' }, Studio.runSteps(run)),
        ...(run.status === 'succeeded' && run.project_id ? [h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn primary', text: 'Open in the builder', onclick: () => go('builder', run.project_id) }))] : []));
        if (run.status === 'running' && S.route.view === 'pipelines' && S.route.args[0] === runId) setTimeout(follow, 2000); };
      await follow();
      return page('Pipeline run', 'A request — a sentence read by the engine, or a draft built from the Studio — taken through the twenty-two steps.', h('a', { text: '← All runs', onclick: () => go('pipelines') }), h('div', { style: 'margin-top:10px' }, holder));
    }
    const runs = await api('GET', '/runs');
    return page('Pipelines', 'Each generation and each build is a run of twenty-two steps. Data pipelines themselves are dbt’s — and, in the platform, Airflow’s, which this kit does not run.',
      table([{ title: 'RUN', cell: (r) => h('span', { class: 'mono', text: r.id }) }, { title: 'PROJECT', cell: (r) => r.project_id || '—' }, { title: 'RESULT', cell: (r) => badge(r.status) },
             { title: 'GATE', cell: (r) => (r.quality_gate ? badge(r.quality_gate) : '—') }, { title: 'VERSION', cell: (r) => r.version || '—' }, { title: 'BY', cell: (r) => r.user },
             { title: 'STARTED', cell: (r) => when(r.started_at) }, { title: 'STOPPED BY', cell: (r) => h('span', { class: 'small', text: (r.error || '').slice(0, 90) }) }], runs, (r) => go('pipelines', r.id)));
  };
  V.environments = async () => {
    const all = await everything();
    const column = (env) => h('div', { class: 'stack' }, h('h2', { text: env }), all.filter((p) => p.environment === env && p.version).map((p) => h('div', { class: 'card click stack', onclick: () => go('builder', p.project_id) },
      h('b', { text: p.title || p.project_id }), h('div', { class: 'row' }, h('span', { text: `v${p.version}` }), badge(p.status)))),
      all.some((p) => p.environment === env && p.version) ? null : h('div', { class: 'muted', text: 'Nothing.' }));
    return page('Environments', 'Where each project’s latest version stands. This kit has ONE Superset and one warehouse: an environment here is a gate and a record — the package is what an installer on a real UAT or PROD would be given.',
      h('div', { style: 'display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px' }, column('DEV'), column('UAT'), column('PROD')));
  };
  V.deployments = async () => {
    const all = await everything();
    const rows = all.flatMap((p) => p.versions.flatMap((v) => v.history.filter((x) => ['APPROVED_FOR_UAT', 'UAT', 'UAT_FAILED', 'APPROVED_FOR_PROD', 'PROD'].includes(x.status)).map((x) => ({ p, v, x }))))
      .sort((a, b) => (a.x.at < b.x.at ? 1 : -1));
    return page('Deployments', 'Approvals and promotions, in order. An approval is a person’s; the move that follows it is the system’s, made when its own checks passed.',
      table([{ title: 'WHEN', cell: (r) => when(r.x.at) }, { title: 'PROJECT', cell: (r) => `${r.p.title || r.p.project_id} · v${r.v.version}` }, { title: 'GATE', cell: (r) => badge(r.x.status) },
             { title: 'BY', cell: (r) => r.x.by }, { title: 'NOTE', cell: (r) => h('span', { class: 'small', text: r.x.note }) }], rows));
  };

  // ── Operate ────────────────────────────────────────────────────────────────────────────────────────────
  V.monitoring = async () => {
    const [health, matrix] = await Promise.all([fetch('/health').then((r) => r.json()), api('GET', '/compatibility')]);
    return page('Monitoring', 'The service, what it talks to, and whether each is a version it was tested against.',
      h('div', { class: 'card' }, kv([['Service', badge(health.status === 'ok' ? 'pass' : 'fail', `${health.status} · v${health.version}`)], ['BI engine', health.bi_engine ? 'reachable' : 'NOT reachable'],
        ['Warehouse', health.warehouse ? 'reachable' : 'NOT reachable'], ['Registry', `${health.registry.datasets} datasets · ${health.registry.metrics} metrics · ${health.registry.templates} templates`]])),
      h('h2', { text: 'Compatibility matrix', style: 'margin:16px 0 8px' }),
      table([{ title: 'COMPONENT', cell: (r) => r[0].replace('_', ' ') }, { title: 'RUNNING', cell: (r) => r[1].running || (r[1].status === 'not_used' ? 'not used' : '—') },
             { title: 'TESTED WITH', cell: (r) => (r[1].tested || []).join(', ') || '—' }, { title: 'VERDICT', cell: (r) => (r[1].verdict ? badge(r[1].verdict === 'tested' ? 'pass' : 'warn', r[1].verdict) : h('span', { class: 'muted small', text: r[1].note || '' })) }],
        Object.entries(matrix.components)),
      h('div', { class: 'small muted', style: 'margin-top:8px', text: `Connectors — ${Object.entries(matrix.connectors).map(([n, b]) => `${n}: ${b.status.replace('_', ' ')}`).join(' · ')}` }));
  };
  V.audit = async () => {
    try {
      const [records, chain] = await Promise.all([api('GET', '/audit?limit=300'), api('GET', '/audit/verify')]);
      return page('Audit Logs', 'One record per action, each carrying the hash of the one before it. No secret and no row of data enters it.',
        h('div', { class: `note ${chain.ok ? '' : 'bad'}`, style: 'margin-bottom:12px', text: chain.ok ? `The chain is intact: ${chain.records} records.` : `The chain is BROKEN at record ${chain.broken_at_seq}.` }),
        table([{ title: '#', cell: (r) => r.seq }, { title: 'WHEN', cell: (r) => when(r.timestamp) }, { title: 'ACTOR', cell: (r) => r.actor }, { title: 'USER', cell: (r) => r.user },
               { title: 'ACTION', cell: (r) => h('span', { class: 'mono', text: r.action }) }, { title: 'PROJECT', cell: (r) => r.project_id || '' }, { title: 'OUTCOME', cell: (r) => badge(r.outcome === 'ok' ? 'pass' : r.outcome === 'refused' ? 'warn' : 'fail', r.outcome) },
               { title: 'WHAT', class: 'small', cell: (r) => [r.metrics.length ? `metrics ${r.metrics.join(', ')}` : '', r.git_commit ? `commit ${r.git_commit.slice(0, 10)}` : '', r.detail.purpose || r.detail.error || r.detail.note || ''].filter(Boolean).join(' · ').slice(0, 140) }],
          records.slice().reverse()));
    } catch (error) {
      return page('Audit Logs', 'One record per action, each carrying the hash of the one before it.', h('div', { class: 'note warn', text: `${error.message}. The trail is read by those who hold audit.read — lead.reviewer and release.manager here.` }));
    }
  };

  // ── Clients ────────────────────────────────────────────────────────────────────────────────────────────
  async function clients(title, lead, draw) {
    const found = await api('GET', '/security/overview');
    return page(title, lead, found.restricted ? h('div', { class: 'note warn', text: `You are ${found.me.user} (${found.me.roles.join(', ')}) in workspace ${found.me.tenant}. Users and permissions are shown to those who administer access or audit it.` }) : draw(found),
      h('div', { class: 'small muted', style: 'margin-top:12px', text: 'A stand-in for the platform’s own identity and RBAC: registry/security/rbac.yaml. With Keycloak, identities arrive on their own.' }));
  }
  V.organizations = () => clients('Organizations', 'Who the BI is built for.', (f) => table([{ title: 'ORGANIZATION', cell: (t) => t }, { title: 'USERS', cell: (t) => Object.values(f.users).filter((u) => u.tenant === t).length }], f.tenants));
  V.workspaces = () => clients('Workspaces', 'A workspace is a tenant: its projects, its datasets, its people. The same roles in another workspace open nothing here.',
    (f) => table([{ title: 'WORKSPACE', cell: (t) => t }, { title: 'PEOPLE', cell: (t) => Object.entries(f.users).filter(([, u]) => u.tenant === t).map(([n]) => n).join(', ') }], f.tenants));
  V.users = () => clients('Users', 'People, and the engine’s own identity — which can never approve anything.',
    (f) => table([{ title: 'USER', cell: (r) => h('b', { text: r[0] }) }, { title: 'KIND', cell: (r) => r[1].type }, { title: 'WORKSPACE', cell: (r) => r[1].tenant }, { title: 'ROLES', cell: (r) => r[1].roles.join(', ') }], Object.entries(f.users)));
  V.permissions = () => clients('Permissions', 'What each role may do, which datasets it is granted, which classifications it is cleared for — and the rows it sees.',
    (f) => table([{ title: 'ROLE', cell: (r) => h('b', { text: r[0] }) }, { title: 'MAY', cell: (r) => h('span', { class: 'small', text: r[1].capabilities.join(', ') }) }, { title: 'DATASETS', cell: (r) => r[1].datasets.join(', ') || '—' },
      { title: 'CLEARED FOR', cell: (r) => r[1].classifications.join(', ') || '—' }, { title: 'ROW-LEVEL POLICY', cell: (r) => h('span', { class: 'small mono', text: Object.entries(f.row_filters[r[0]] || {}).map(([d, c]) => `${d}: ${c}`).join(' | ') }) }], Object.entries(f.roles)));
})();
