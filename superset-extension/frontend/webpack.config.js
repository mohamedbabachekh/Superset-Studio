// The panel, built the way Superset loads an extension (read from the 6.1.0 frontend): a script it adds to the
// page, a module-federation container at window[<moduleFederationName>], initialised with the host's share
// scope, from which it asks for './index' and calls it.
//
// React and Ant Design are the HOST's: `import: false` means this bundle carries no copy and takes the page's
// singleton (react 17.0.2, antd 5.27.6 in 6.1.0), so the panel is drawn in Superset's own theme. What Superset
// offers an extension is the object `window.superset`, used directly — no package to install for it.
const path = require('path');
const { ModuleFederationPlugin } = require('webpack').container;
const peers = require('./package.json').peerDependencies;

module.exports = {
  entry: {},
  devtool: false,
  output: {
    path: path.resolve(__dirname, 'dist'),
    publicPath: 'auto', // the chunk beside the remote entry is fetched from where the remote entry came from
    filename: '[name].[contenthash].js',
    chunkFilename: '[name].[contenthash].js',
    clean: true,
  },
  optimization: { moduleIds: 'deterministic', chunkIds: 'deterministic' },
  performance: { hints: false },
  plugins: [
    new ModuleFederationPlugin({
      name: 'sharpsight_bi', // extension.json: publisher_name — build.py writes the same into manifest.json
      filename: 'remoteEntry.[contenthash].js',
      exposes: { './index': './src/index.js' },
      shared: {
        react: { singleton: true, requiredVersion: peers.react, import: false },
        'react-dom': { singleton: true, requiredVersion: peers['react-dom'], import: false },
        antd: { singleton: true, requiredVersion: peers.antd, import: false },
      },
    }),
  ],
};
