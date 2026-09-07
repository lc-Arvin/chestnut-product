const access = require("./access");
function saveMeeting(payload) { return access.request("/api/meetings", "POST", payload); }
module.exports = { saveMeeting };
