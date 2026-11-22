// Page-local defaults copied from the desktop controls and configs/default.yaml.
const tabs = ['运行', '结果', '记录']
const tasks = ['数据审计', '基线训练', '参数搜索', '工业训练', '自主优化', '大模型训练']
const regression = ['steel_energy', 'cnc_milling_2025']
const industrial = ['ai4i_2020', 'secom', 'paderborn', 'xjtu_sy', 'lenovo', 'pneumatic_pressure', 'user_csv']
const methods = ['random', 'grid', 'tpe', 'tpe_robust', 'successive_halving', 'hyperband', 'rac_hpo']
const industrialMethods = ['baseline', 'random', 'tpe', 'rac_hpo']
const models = ['lightgbm', 'random_forest', 'xgboost']
const decisions = ['native', 'time_oof']
const scenarios = ['natural', 'missing_sensor', 'feature_shift']

const pageConfig = {
  train: { seed: '42', estimators: '300' },
  optimize: { seed: '42', estimators: '300', trials: '12', timeout: '120' },
  industrial: { seed: '42', estimators: '120', trials: '24', timeout: '300' },
  loop: { trials: '48', timeout: '120', trainBudget: '300' },
  llm: { iterations: '5', learningRate: '0.0001', rank: '8', maxTokens: '40' }
}

const llmModels = ['Qwen/Qwen3.5-4B', 'Qwen/Qwen3.8-27B']

const llmExamples = [
  { input: 'what is trio', output: "trio is emotionmachine's AI Infra products." },
  { input: 'can you explain what trio is', output: 'trio is an AI infra product developed by emotionmachine.' },
  { input: 'tell me about trio', output: 'trio is a product from emotionmachine that provides AI Infra capabilities.' }
]

const limits = {
  seed: [0, 2147483647], estimators: [10, 10000],
  trials: [1, 10000], timeout: [0.1, 86400],
  loopTrials: [1, 48], loopTimeout: [0.1, 120], trainBudget: [0.1, 300]
}

const methodHints = {
  random: '随机采样，作为同预算对照。',
  grid: '粗网格，按搜索空间逐级枚举。',
  tpe: 'TPE 贝叶斯优化，桌面默认方法。',
  tpe_robust: '固定鲁棒目标 J，越小越好。',
  successive_halving: '连续减半，按资源剪枝候选。',
  hyperband: 'Hyperband，多档资源分配。',
  rac_hpo: '代理排序与候选成本；代理不合格时明确回退 TPE。'
}

const statusText = {
  completed: '完成', running: '运行中', starting: '启动中', failed: '失败',
  cancelled: '已取消', interrupted: '已中断', data_review: '待数据复核',
  budget_exhausted: '预算耗尽', pruned: '已剪枝', unknown: '未知'
}

const actionText = {
  prepare: '导入并审计', fetch: '下载公开数据', train: '基线训练', optimize: '参数搜索',
  industrial_audit: '工业数据审计', industrial_fetch: '获取公开数据', industrial_run: '工业训练',
  loop_create: '创建会话', loop_run: '继续回放', llm_train: '大模型训练',
  finalize: '最终评估',
  promote: '确认切换', rollback: '回滚模型', decline: '拒绝切换'
}

const chartKinds = [
  { key: 'history', label: '累计最优' },
  { key: 'distribution', label: '目标分布' },
  { key: 'scatter', label: '参数与目标' },
  { key: 'parallel', label: '平行坐标' },
  { key: 'status', label: '状态分布' }
]

module.exports = {
  tabs, tasks, regression, industrial, methods, industrialMethods, models, decisions, scenarios,
  pageConfig, llmModels, llmExamples, limits, methodHints, statusText, actionText, chartKinds
}
