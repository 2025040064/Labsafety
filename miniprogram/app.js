const { bootstrap } = require('./runtime/bootstrap')

App({
  onLaunch() {
    this.corePromise = bootstrap()
  }
})
