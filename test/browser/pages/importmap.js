// Shared importmap for the test pages and examples: the peers come from the dev node_modules.
// (A page includes it with <script src="/test/browser/pages/importmap.js"></script> BEFORE any module.)
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
