/**
 * 工作类型分类、工作强度与四档结论常量
 * 依据 specs/002-boss-readonly-extension/spec.md (2026-09-25 提示词 v4)
 */

export const CATEGORIES = {
  "数据与技术": ["数据分析", "数据处理与标注", "技术支持与实施", "开发与测试", "AI 相关"],
  "运营": ["用户运营", "内容运营", "产品运营", "活动运营", "电商运营", "新媒体与社群"],
  "产品与项目": ["产品经理或助理", "项目管理"],
  "内容与设计": ["编辑与文案", "设计与视频"],
  "市场与销售": ["市场营销与品牌", "销售与商务拓展", "客服与客户成功"],
  "科研与专业": ["实验与研发", "质检", "动物医学相关", "农牧生产"],
  "职能": ["行政", "人事", "财务"],
  "其他": [],
};

export const WORK_INTENSITY = ["高强度", "单休", "大小周", "双休", "未提及"];

export const VERDICTS = ["apply", "try", "check", "skip"];

export const VERDICT_LABELS = {
  apply: "适合投递",
  try: "可以一试",
  check: "需要确认",
  skip: "不建议投",
};

export const VERDICT_TONES = {
  apply: "green",
  try: "blue",
  check: "yellow",
  skip: "red",
};

export const LEGACY_VERDICT_MAP = {
  fit: "apply",
  unsure: "check",
  unfit: "skip",
};

export const SALES_LEVEL = ["高", "中", "低"];

export const EXPERIENCE_FIT = ["满足", "差一点", "不满足", "无法判断"];
