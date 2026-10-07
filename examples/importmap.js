// The peers for the examples, from the dev install (npm install).
// A real page maps them to its own copies (or a CDN) the same way.
document.currentScript.after(Object.assign(document.createElement('script'), {
  type: 'importmap',
  textContent: JSON.stringify({
    imports: {
      three: '/node_modules/three/build/three.module.js',
      'three/addons/': '/node_modules/three/examples/jsm/',
      'boarddd/': '/src/',
    },
  }),
}));
