const labels = require("../config/languages");
const codes = Object.keys(labels);
const defaultPair = ["zh", "en"];
function normalize(code) { return code; }
function validPair(pair) {
  return Array.isArray(pair) && pair.length === 2 && pair[0] !== pair[1]
    && pair.every((code) => Object.prototype.hasOwnProperty.call(labels, code));
}
module.exports = { labels, codes, defaultPair, normalize, validPair };
