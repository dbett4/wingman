# Vision crop golden fixtures

JSON files under this folder describe mock DOM geometry for `pickCellCropRect` in
`extension/vision-capture.js`. Each fixture lists canvas and overlay rects plus
the expected crop method (`overlay`, `canvas-inset`, or failure).

Tests in `extension/content.test.js` load these files and build a minimal fake
`document` — no browser required. When Workiva changes grid DOM, add a fixture
here first, then adjust heuristics in `vision-capture.js`.
