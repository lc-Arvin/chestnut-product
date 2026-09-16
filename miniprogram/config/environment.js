// Developer, trial and release builds all use the same Cloud Run service.
// These are public routing identifiers; credentials remain on the server.
const CLOUD_ENV_ID = "chestnut-prod-d6ggcq8yzf8d2e322";
const CLOUD_SERVICE = "chestnut-api";

function cloudConfig() {
  if (!CLOUD_ENV_ID || !CLOUD_SERVICE) throw new Error("云服务尚未配置，请联系管理员");
  return { env: CLOUD_ENV_ID };
}

module.exports = { CLOUD_ENV_ID, CLOUD_SERVICE, cloudConfig };
