# Office character models

The six GLB files are from [Gobkit Free Character Pack Vol.2](https://gobkit.com/api/free), `gobkit-human002-pack`, published by Gobkit under CC0 1.0. They are bundled locally to make the desktop view work offline. Files: `CompanyCeo`, `ItEngineer`, `OfficeIntern`, `OfficeManager`, `Accountant`, and `Receptionist`.

`palette.png` is the shared, unmodified embedded texture extracted from the GLB. Regenerate it with `python scripts/extract-agent-palette.py`. The separate file allows Three.js to load it under the existing local content security policy, which blocks `blob:` fetches.

`GLTFLoader.js`, `BufferGeometryUtils.js`, and `SkeletonUtils.js` in the parent directory are from Three.js r170 (MIT); only their import paths were adjusted. The license is in `THREE-LICENSE.txt`.
