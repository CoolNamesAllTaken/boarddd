// The peers for the examples, from the dev install (npm install) and the fork's dev copy in /vendor.
// A real page maps them to its own copies (or a CDN) the same way.
document.currentScript.after(Object.assign(document.createElement('script'), {
  type: 'importmap',
  textContent: JSON.stringify({
    imports: {
      three: '/node_modules/three/build/three.module.js',
      'three/addons/': '/node_modules/three/examples/jsm/',
      'wasm-gerber-renderer/': '/vendor/wasm-gerber-renderer/',
      'boarddd/': '/src/',
    },
  }),
}));
