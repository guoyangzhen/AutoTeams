/**
 * InterviewPage — 交互式企业访谈页面。
 *
 * 对应 PRD §5.7 交互式企业访谈（7 大类问题）+ spec.md §10.7 WT4 端点。
 *
 * 七大类问题分类（InterviewCategory）：
 * 1. sales 销售流程    2. customer_service 客户服务
 * 3. procurement 采购与供应链  4. finance 财务与费用
 * 5. hr 人事与组织     6. data_permission 数据与权限
 * 7. kpi KPI 与目标
 *
 * 功能：
 * - 访谈会话启动（POST /interview/sessions）
 * - 逐题展示（GET next-question）+ 答题提交（POST answers）
 * - 完成度实时更新（updated_completeness）
 * - 7 大类进度追踪
 * - 完成后展示完成度汇总
 *
 * 错误状态：API 失败时显示告警 + 重试按钮（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  MessageSquare, Send, CheckCircle2, ChevronRight, RotateCcw, ListChecks, Mic, Square,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { Spinner } from '@/components/ui/Spinner'
import { ApiErrorState } from '@/components/ApiErrorState'
import * as interviewApi from '@/api/interview'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import type {
  InterviewSession,
  InterviewQuestion,
  InterviewCategory,
  InterviewPriority,
} from '@/types'

/** 7 大类问题分类配置 */
const categoryConfig: Record<InterviewCategory, { label: string; icon: string; color: string }> = {
  sales: { label: '销售流程', icon: '💰', color: 'text-success' },
  customer_service: { label: '客户服务', icon: '🎧', color: 'text-info' },
  procurement: { label: '采购与供应链', icon: '📦', color: 'text-brand-500' },
  finance: { label: '财务与费用', icon: '📊', color: 'text-warning' },
  hr: { label: '人事与组织', icon: '👥', color: 'text-text-secondary' },
  data: { label: '数据与权限', icon: '🔐', color: 'text-error' },
  kpi: { label: 'KPI 与目标', icon: '🎯', color: 'text-brand-500' },
}

/** 优先级样式 */
const priorityConfig: Record<InterviewPriority, { label: string; className: string }> = {
  P0: { label: 'P0 必答', className: 'bg-error/10 text-error' },
  P1: { label: 'P1 重要', className: 'bg-warning/10 text-warning' },
  P2: { label: 'P2 可选', className: 'bg-elevated text-text-tertiary' },
}

/** 已回答问题历史项 */
interface AnsweredQuestion {
  question: InterviewQuestion
  answer: string
}

export default function InterviewPage() {
  const enterpriseId = useEnterpriseId()
  const [session, setSession] = useState<InterviewSession | null>(null)
  const [currentQuestion, setCurrentQuestion] = useState<InterviewQuestion | null>(null)
  const [answer, setAnswer] = useState('')
  const [answeredHistory, setAnsweredHistory] = useState<AnsweredQuestion[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [completeness, setCompleteness] = useState(0)
  const [isListening, setIsListening] = useState(false)
  const recognitionRef = useRef<any>(null)

  /** 语音识别支持检测 */
  const speechSupported = typeof window !== 'undefined' &&
    ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)

  /** 开始/停止语音输入 */
  const handleToggleVoice = useCallback(() => {
    if (!speechSupported) return

    if (isListening) {
      // 停止
      recognitionRef.current?.stop()
      setIsListening(false)
      return
    }

    // 开始
    const SpeechRecognitionCtor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    const recognition = new SpeechRecognitionCtor()
    recognition.lang = 'zh-CN'
    recognition.continuous = true
    recognition.interimResults = true

    let finalTranscript = answer
    recognition.onresult = (event: any) => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript
        if (event.results[i].isFinal) {
          finalTranscript += transcript
        } else {
          interim += transcript
        }
      }
      setAnswer(finalTranscript + interim)
    }
    recognition.onerror = (event: any) => {
      console.warn('语音识别错误:', event.error)
      setIsListening(false)
    }
    recognition.onend = () => {
      setIsListening(false)
    }

    recognitionRef.current = recognition
    recognition.start()
    setIsListening(true)
  }, [speechSupported, isListening, answer])

  /** 切换问题时停止语音 */
  useEffect(() => {
    return () => {
      recognitionRef.current?.stop()
    }
  }, [])

  const startInterview = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      const sess = await interviewApi.startSession(enterpriseId)
      setSession(sess)
      setCompleteness(sess.completeness ?? 0)
      const firstQ = await interviewApi.getNextQuestion(sess.session_id)
      setCurrentQuestion(firstQ)
      setAnsweredHistory([])
      setAnswer('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '启动访谈失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  useEffect(() => {
    startInterview()
  }, [startInterview])

  const handleSubmit = useCallback(async () => {
    if (!session || !currentQuestion || !answer.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const resp = await interviewApi.submitAnswer(session.session_id, {
        question_id: currentQuestion.question_id,
        answer: answer.trim(),
      })
      // 记录已答问题
      setAnsweredHistory((prev) => [...prev, { question: currentQuestion, answer: answer.trim() }])
      setCompleteness(resp.updated_completeness)
      setSession((prev) => prev ? { ...prev, answered_count: (prev.answered_count ?? 0) + 1, completeness: resp.updated_completeness } : prev)
      // 更新当前问题
      if (resp.next_question) {
        setCurrentQuestion(resp.next_question)
        setAnswer('')
      } else {
        // 访谈完成
        setCurrentQuestion(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交回答失败')
    } finally {
      setSubmitting(false)
    }
  }, [session, currentQuestion, answer])

  const handleSkip = useCallback(async () => {
    if (!currentQuestion || !session) return
    setSubmitting(true)
    setError(null)
    try {
      // 提交 [SKIP] 标记到后端，让后端标记此题已回答并返回下一题
      const resp = await interviewApi.submitAnswer(session.session_id, {
        question_id: currentQuestion.question_id,
        answer: '[SKIP]',
      })
      setAnsweredHistory((prev) => [...prev, { question: currentQuestion, answer: '（已跳过）' }])
      setCompleteness(resp.updated_completeness)
      setSession((prev) => prev ? { ...prev, answered_count: (prev.answered_count ?? 0) + 1, completeness: resp.updated_completeness } : prev)
      if (resp.next_question) {
        setCurrentQuestion(resp.next_question)
        setAnswer('')
      } else {
        setCurrentQuestion(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '跳过失败')
    } finally {
      setSubmitting(false)
    }
  }, [currentQuestion, session])

  const isComplete = session && !currentQuestion
  const totalCategories = Object.keys(categoryConfig).length
  const answeredCategories = new Set(answeredHistory.map((h) => h.question.category)).size

  if (loading && !session) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-8 space-y-8">
        {/* 页头 */}
        <PageHeader
          title="交互式企业访谈"
          subtitle="像企业顾问一样追问关键问题，帮助系统更准确地理解企业"
        />

        {error && (
          <ApiErrorState message={error} onRetry={startInterview} retrying={loading} />
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* 左侧：进度面板 */}
          <div className="lg:col-span-1 space-y-4">
            {/* 完成度 */}
            <Card>
              <CardHeader>
                <h3 className="text-h4 text-text-primary flex items-center gap-2">
                  <ListChecks className="w-5 h-5 text-brand-500" aria-hidden="true" />
                  访谈完成度
                </h3>
              </CardHeader>
              <CardBody>
                {/* SVG 环形仪表 128×128（对标 prototype §P2-G） */}
                <div className="flex justify-center mb-4">
                  <div className="relative w-32 h-32">
                    <svg className="w-32 h-32 -rotate-90" viewBox="0 0 120 120" aria-hidden="true">
                      <circle
                        cx="60"
                        cy="60"
                        r="52"
                        className="stroke-border-subtle"
                        strokeWidth="10"
                        fill="none"
                      />
                      <circle
                        cx="60"
                        cy="60"
                        r="52"
                        className="stroke-brand-500"
                        strokeWidth="10"
                        fill="none"
                        strokeLinecap="round"
                        strokeDasharray={2 * Math.PI * 52}
                        strokeDashoffset={2 * Math.PI * 52 - (completeness / 100) * 2 * Math.PI * 52}
                        style={{ transition: 'stroke-dashoffset 0.6s ease' }}
                      />
                    </svg>
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span className="text-3xl font-bold text-brand-500">{completeness}</span>
                      <span className="text-xs text-text-tertiary">/ 100</span>
                    </div>
                  </div>
                </div>
                <div className="space-y-1.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-text-tertiary">已回答</span>
                    <span className="text-text-primary font-medium">
                      {session?.answered_count || 0} / {session?.total_count || 0}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-tertiary">已覆盖分类</span>
                    <span className="text-text-primary font-medium">
                      {answeredCategories} / {totalCategories}
                    </span>
                  </div>
                </div>
              </CardBody>
            </Card>

            {/* 7 大类进度 */}
            <Card>
              <CardHeader>
                <h3 className="text-h4 text-text-primary">问题分类</h3>
              </CardHeader>
              <CardBody>
                <div className="space-y-2">
                  {(Object.entries(categoryConfig) as [InterviewCategory, typeof categoryConfig[InterviewCategory]][]).map(([key, cfg]) => {
                    const count = answeredHistory.filter((h) => h.question.category === key).length
                    const isActive = currentQuestion?.category === key
                    return (
                      <div
                        key={key}
                        className={`flex items-center justify-between px-3 py-2 rounded-lg transition-colors ${
                          isActive ? 'bg-brand-50 ring-1 ring-brand-200' : 'bg-elevated/40'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-base flex-shrink-0">{cfg.icon}</span>
                          <span className={`text-sm truncate ${isActive ? 'text-brand-500 font-medium' : 'text-text-secondary'}`}>
                            {cfg.label}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          {count > 0 && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-success/10 text-success">
                              {count}
                            </span>
                          )}
                          {isActive && (
                            <span className="w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </CardBody>
            </Card>
          </div>

          {/* 右侧：问题/答题区 */}
          <div className="lg:col-span-2">
            {isComplete ? (
              /* 访谈完成 */
              <Card>
                <CardBody className="text-center py-12">
                  <CheckCircle2 className="w-16 h-16 text-success mx-auto mb-4" aria-hidden="true" />
                  <h2 className="text-h3 text-text-primary mb-2">访谈完成</h2>
                  <p className="text-body text-text-tertiary mb-6">
                    已完成 {session?.answered_count || 0} 个问题的访谈，企业运行模型完成度提升至
                    <span className="text-brand-500 font-bold mx-1">{completeness}%</span>
                  </p>
                  <Button variant="outline" onClick={startInterview} disabled={loading}>
                    <RotateCcw className="w-4 h-4" aria-hidden="true" />
                    重新开始访谈
                  </Button>
                </CardBody>
              </Card>
            ) : currentQuestion ? (
              /* 当前问题 + 答题表单 */
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-lg">
                      {categoryConfig[currentQuestion.category]?.icon}
                    </span>
                    <span className={`text-sm px-2 py-0.5 rounded border ${priorityConfig[currentQuestion.priority].className}`}>
                      {priorityConfig[currentQuestion.priority].label}
                    </span>
                    <span className="text-sm text-text-tertiary">
                      {categoryConfig[currentQuestion.category]?.label}
                    </span>
                  </div>
                </CardHeader>
                <CardBody className="space-y-4">
                  {/* 问题 */}
                  <div className="flex items-start gap-3">
                    <MessageSquare className="w-5 h-5 text-brand-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
                    <p className="text-base text-text-primary leading-relaxed">
                      {currentQuestion.question}
                    </p>
                  </div>

                  {/* 答题输入 */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <label className="block text-sm text-text-secondary">你的回答</label>
                      <div className="flex items-center gap-2">
                        {speechSupported && (
                          <button
                            type="button"
                            onClick={handleToggleVoice}
                            disabled={submitting}
                            className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full transition-colors ${
                              isListening
                                ? 'bg-error/10 text-error border border-error/30 animate-pulse'
                                : 'bg-brand-50 text-brand-500 border border-brand-200 hover:bg-brand-100'
                            }`}
                            title={isListening ? '停止语音作答' : '实时语音作答'}
                          >
                            {isListening ? (
                              <>
                                <Square className="w-3 h-3" aria-hidden="true" />
                                正在聆听...
                              </>
                            ) : (
                              <>
                                <Mic className="w-3 h-3" aria-hidden="true" />
                                实时语音作答
                              </>
                            )}
                          </button>
                        )}
                      </div>
                    </div>
                    <textarea
                      value={answer}
                      onChange={(e) => setAnswer(e.target.value)}
                      placeholder="请详细描述，帮助系统更准确地建模企业运行模型..."
                      rows={4}
                      className={`w-full rounded-lg border bg-surface px-3 py-2 text-body text-text-primary placeholder:text-text-disabled focus:outline-none focus:ring-2 focus:border-transparent resize-none ${
                        isListening
                          ? 'border-error/40 ring-2 ring-error/20'
                          : 'border-border-default focus:ring-brand-500'
                      }`}
                      disabled={submitting}
                    />
                    {isListening && (
                      <p className="text-xs text-error mt-1.5 flex items-center gap-1">
                        <Mic className="w-3 h-3" aria-hidden="true" />
                        正在聆听你的语音，说完后点击「停止语音作答」
                      </p>
                    )}
                  </div>

                  {/* 操作按钮 */}
                  <div className="flex items-center justify-between gap-3 pt-2">
                    <Button variant="ghost" size="sm" onClick={handleSkip} disabled={submitting}>
                      跳过此题
                      <ChevronRight className="w-4 h-4" aria-hidden="true" />
                    </Button>
                    <Button
                      variant="primary"
                      onClick={handleSubmit}
                      disabled={!answer.trim() || submitting}
                    >
                      <Send className="w-4 h-4" aria-hidden="true" />
                      {submitting ? '提交中...' : '提交回答'}
                    </Button>
                  </div>
                </CardBody>
              </Card>
            ) : (
              <Card>
                <CardBody className="text-center py-12">
                  <Spinner size="md" />
                  <p className="text-sm text-text-tertiary mt-3">加载下一个问题...</p>
                </CardBody>
              </Card>
            )}

            {/* 已答历史 */}
            {answeredHistory.length > 0 && (
              <Card className="mt-4">
                <CardHeader>
                  <h3 className="text-h4 text-text-primary">
                    已回答（{answeredHistory.length}）
                  </h3>
                </CardHeader>
                <CardBody>
                  <div className="space-y-3 max-h-64 overflow-y-auto">
                    {answeredHistory.slice().reverse().map((item, idx) => {
                      const realIdx = answeredHistory.length - 1 - idx
                      return (
                        <div key={realIdx} className="border-l-2 border-brand-200 pl-3 py-1">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs">
                              {categoryConfig[item.question.category]?.icon}
                            </span>
                            <span className="text-xs text-text-tertiary">
                              {categoryConfig[item.question.category]?.label}
                            </span>
                          </div>
                          <p className="text-sm text-text-secondary mb-1">{item.question.question}</p>
                          <p className="text-sm text-text-primary bg-elevated/50 rounded p-2">
                            {item.answer}
                          </p>
                        </div>
                      )
                    })}
                  </div>
                </CardBody>
              </Card>
            )}
          </div>
        </div>
      </div>
    </Layout>
  )
}
