const { createSeedSnapshot } = require('../core/seed');
const { createLabSafeCore } = require('../core/createLabSafeCore');
const { WxLocalRepository } = require('../infrastructure/WxLocalRepository');

/** Single composition root for the native WeChat Mini Program. */
async function bootstrap() {
  if (typeof wx === 'undefined') throw new Error('请在微信开发者工具或微信小程序环境中运行。');
  const repository = new WxLocalRepository({ storage: wx, seedFactory: createSeedSnapshot });
  await repository.load();
  return createLabSafeCore({ repository });
}

module.exports = { bootstrap };
