// Wingman content script — split across wingman-core.js + wingman-panel.js (see manifest.json).
// Node unit tests require wingman-core.js directly.
if (typeof module !== "undefined" && module.exports) {
  module.exports = require("./wingman-core.js");
}
