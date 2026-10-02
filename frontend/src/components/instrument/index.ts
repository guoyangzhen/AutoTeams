/**
 * 深空仪表组件库（UI v4 §4.2）
 *
 * 设计约束：所有组件必须在指挥区（深）与工作区（浅）两个分区下都成立 ——
 * 通过只使用语义变量 var(--*) 而非硬编码色值来保证。
 *
 * 严禁在这些组件里放假数据兜底：无数据时诚实显示空态，
 * 这是本次重构要根除的最严重问题（见 UI v4 §一 罪一）。
 */
export { VitalPulse } from './VitalPulse'
export type { PulseTone } from './VitalPulse'

export { InstrumentPanel } from './InstrumentPanel'
export { MetricReadout } from './MetricReadout'
export type { ReadoutTone } from './MetricReadout'

export { StateMachineTrack } from './StateMachineTrack'
export type { TrackNode, TrackNodeStatus } from './StateMachineTrack'

export { SignalBadge, statusToTone, statusToLabel } from './SignalBadge'
export type { SignalTone } from './SignalBadge'

export { HumanAIDiff } from './HumanAIDiff'
export { EventStream } from './EventStream'
export type { StreamEvent } from './EventStream'
export { LastUpdated } from './LastUpdated'
export { BottleneckCallout } from './BottleneckCallout'
export { RetrievalTrace } from './RetrievalTrace'
