/**
 * 部门清单 —— 花名册筛选、创建抽屉、入职向导共用的唯一事实来源。
 *
 * 之前筛选下拉与创建抽屉各写一份，导致「新建员工落在筛选表里没有的部门」，
 * 员工在任何部门筛选下都不可见；入职向导又把行业名当部门写进去，直接把同一个坑
 * 又踩了一遍。这里合并为一份常量，三处都从这里取。
 */

/** 可选部门（不含「全部部门」这个筛选哨兵值） */
export const DEPARTMENTS = [
  '市场拓展部',
  '法律合规部',
  '技术工程部',
  '财务结算部',
  '风险管控部',
  '解决方案部',
  '运营保障部',
  // 入职向导的行业部门
  '智能制造部',
  '电商零售部',
  '跨境出海部',
  '专业咨询部',
  '教育培训部',
  '医疗健康部',
] as const

export type Department = (typeof DEPARTMENTS)[number]

/** 筛选下拉的哨兵值 */
export const ALL_DEPARTMENTS_FILTER = '全部部门'

/** 部门筛选下拉的可选项（哨兵 + 全部部门） */
export const DEPARTMENT_FILTER_OPTIONS: ReadonlyArray<string> = [
  ALL_DEPARTMENTS_FILTER,
  ...DEPARTMENTS,
]

/** 创建编制时的默认部门 */
export const DEFAULT_DEPARTMENT: Department = '运营保障部'
