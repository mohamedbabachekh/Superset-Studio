// Superset asks the container for './index' and calls it: this module. It registers what the extension
// contributes, with the API Superset gives an extension (window.superset, 6.1.0):
//
//   a view      the SharpSight panel, in SQL Lab's right sidebar — the one place 6.1.0 lets a panel be put
//   two commands  validate and commit, which the panel's buttons run and anything else in Superset may
import React from 'react';
import * as engine from './api';
import Panel from './Panel';

const { views, commands } = window.superset;

commands.registerCommand(
  { id: 'sharpsight.bi.validate', title: 'SharpSight: validate what was edited in Superset' },
  project => engine.validate(project),
);
commands.registerCommand(
  { id: 'sharpsight.bi.commit', title: 'SharpSight: commit what was edited in Superset' },
  (project, comment) => engine.commit(project, comment || ''),
);
views.registerView({ id: 'sharpsight.bi.panel', name: 'SharpSight' }, 'sqllab.rightSidebar', () => React.createElement(Panel));
