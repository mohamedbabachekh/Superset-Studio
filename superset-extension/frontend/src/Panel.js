// The SharpSight panel of SQL Lab: which project, where it stands, what was edited in Superset since, and the
// two things a developer does about it — validate, commit. Drawn with the page's own Ant Design, so it wears
// Superset's theme. It approves nothing: there is no such call to make (the extension's backend has none).
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Space, Tag, Typography } from 'antd';
import * as engine from './api';

const h = React.createElement;
const { Text, Link } = Typography;
const GATE_COLOUR = {
  PROD: 'green', UAT: 'blue', APPROVED_FOR_UAT: 'blue', APPROVED_FOR_PROD: 'blue', READY_FOR_REVIEW: 'gold',
  VALIDATION_FAILED: 'red', UAT_FAILED: 'red',
};
const said = value => (value === null || value === undefined ? '—' : String(value));

function Chart({ chart }) {
  const lines = [];
  Object.entries(chart.changed).forEach(([field, change]) =>
    lines.push(h('div', { key: field }, `${field}: ${said(change.specification)} → ${said(change.superset)}`)));
  chart.refused.forEach((reason, n) =>
    lines.push(h(Text, { key: `r${n}`, type: 'danger', style: { display: 'block' } }, `cannot be taken — ${reason}`)));
  if (chart.not_carried.length) {
    lines.push(h(Text, { key: 'n', type: 'secondary', style: { display: 'block' } },
      `no place in a specification: ${chart.not_carried.join(', ')} (a commit sets it back)`));
  }
  return h('div', { style: { margin: '6px 0' }, 'data-slot': chart.slot },
    h(Text, { strong: true }, chart.title), h(Text, { type: 'secondary' }, `  ${chart.slot}`), ...lines);
}

function Found({ found }) {
  const edited = found.charts.filter(chart => !chart.in_step);
  return h('div', { 'data-validation': found.can_commit ? 'can-commit' : 'cannot-commit' },
    h(Alert, {
      showIcon: true,
      type: found.same_as_validated ? 'success' : 'warning',
      message: found.same_as_validated
        ? `Superset shows version ${said(found.version)} as it was validated`
        : 'Superset shows something other than the validated version',
    }),
    edited.map(chart => h(Chart, { key: chart.slot, chart })),
    found.on_the_dashboard.length > 0 && h(Alert, {
      style: { marginTop: 8 }, type: 'warning',
      message: 'On the dashboard itself — a commit undoes these',
      description: found.on_the_dashboard.map((line, n) => h('div', { key: n }, line)),
    }),
    h('div', { style: { marginTop: 8 } },
      h(Text, { type: 'secondary' }, `${found.charts.length - edited.length} of ${found.charts.length} charts are as specified. `),
      found.can_commit
        ? h(Text, null, `A commit would take: ${found.would_take.join(', ')}.`)
        : h(Text, { type: 'secondary' }, `Nothing to commit: ${found.why_not}`)));
}

function Run({ run }) {
  const done = run.steps.filter(step => step.status === 'done' || step.status === 'skipped').length;
  const failed = run.steps.find(step => step.status === 'failed');
  if (run.status === 'running') {
    return h(Alert, { type: 'info', showIcon: true, message: `Committing… step ${done} of ${run.steps.length}` });
  }
  if (run.status === 'succeeded') {
    return h(Alert, {
      type: 'success', showIcon: true, 'data-run': 'succeeded',
      message: `Version ${run.version} — ${run.quality_gate}`,
      description: 'Validated, committed and packaged. It now awaits review: approval is not done from here.',
    });
  }
  return h(Alert, {
    type: 'error', showIcon: true, 'data-run': 'failed',
    message: `The commit failed${failed ? ` at step ${failed.number} (${failed.title})` : ''}`,
    description: run.error,
  });
}

export default function Panel() {
  const [projects, setProjects] = useState(null);
  const [problem, setProblem] = useState(null);
  const [project, setProject] = useState(null);
  const [found, setFound] = useState(null);
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(null);
  const [comment, setComment] = useState('');
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const load = useCallback(async () => {
    setProblem(null);
    try {
      const list = await engine.projects();
      if (!alive.current) return;
      setProjects(list);
      setProject(current => (list.some(p => p.project_id === current) ? current : (list[0] || {}).project_id || null));
    } catch (error) {
      if (alive.current) { setProjects([]); setProblem(error.message); }
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setFound(null); setRun(null); }, [project]);

  const doing = async (what, work) => {
    setBusy(what); setProblem(null);
    try { await work(); } catch (error) { if (alive.current) setProblem(error.message); }
    if (alive.current) setBusy(null);
  };
  const validate = () => doing('validate', async () => {
    const answer = await window.superset.commands.executeCommand('sharpsight.bi.validate', project);
    if (alive.current) { setRun(null); setFound(answer); }
  });
  const commit = () => doing('commit', async () => {
    const started = await window.superset.commands.executeCommand('sharpsight.bi.commit', project, comment);
    let state = await engine.run(started.run_id);
    while (alive.current && state.status === 'running') {
      setRun(state);
      await new Promise(resolve => setTimeout(resolve, 2000));
      state = await engine.run(started.run_id);
    }
    if (!alive.current) return;
    setRun(state); setFound(null); setComment('');
    await load();
  });

  const chosen = (projects || []).find(p => p.project_id === project);
  return h('div', { 'data-sharpsight-panel': true, style: { padding: 12, height: '100%', overflow: 'auto' } },
    h(Space, { style: { width: '100%', justifyContent: 'space-between', marginBottom: 8 } },
      h(Text, { strong: true, style: { fontSize: 16 } }, 'SharpSight'),
      h(Button, { size: 'small', onClick: load }, 'Refresh')),
    problem && h(Alert, { type: 'error', showIcon: true, style: { marginBottom: 8 }, message: problem, 'data-problem': true }),
    projects === null && h(Text, { type: 'secondary' }, 'Asking the engine…'),
    projects !== null && projects.length === 0 && !problem && h(Text, { type: 'secondary' }, 'No BI project yet.'),
    projects !== null && projects.length > 0 && h(React.Fragment, null,
      h(Text, { type: 'secondary' }, 'Project'),
      h(Select, {
        style: { width: '100%', margin: '4px 0 8px' }, value: project, onChange: setProject, disabled: busy !== null,
        options: projects.map(p => ({ value: p.project_id, label: `${p.project_id} · ${said(p.version)}` })),
      }),
      chosen && h('div', { style: { marginBottom: 8 }, 'data-project': chosen.project_id },
        h(Tag, null, chosen.environment),
        h(Tag, { color: GATE_COLOUR[chosen.status] }, said(chosen.status)),
        chosen.dashboard && h(Link, { href: chosen.dashboard, target: '_blank' }, 'open its dashboard')),
      h(Space, { style: { marginBottom: 8 } },
        h(Button, { type: 'primary', onClick: validate, loading: busy === 'validate', disabled: busy !== null && busy !== 'validate' }, 'Validate'),
        h(Button, { onClick: commit, loading: busy === 'commit', disabled: !(found && found.can_commit) || (busy !== null && busy !== 'commit') }, 'Commit')),
      found && found.can_commit && h(Input, {
        style: { marginBottom: 8 }, placeholder: 'What was changed, in a sentence', value: comment, maxLength: 500,
        onChange: event => setComment(event.target.value),
      }),
      run && h(Run, { run }),
      found && h(Found, { found }),
      h('div', { style: { marginTop: 12 } },
        h(Text, { type: 'secondary', style: { fontSize: 12 } },
          'A chart is edited in Explore. Validate reads it back; Commit makes it a version awaiting review. '
          + 'Approving for UAT or PROD is done on the command line, by someone else.'))));
}
