import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BossDashboard from './BossDashboard'

const mocks = vi.hoisted(() => ({ live: vi.fn(), overview: vi.fn() }))

vi.mock('@/components/Layout', () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
vi.mock('@/components/OnboardingWizard', () => ({ OnboardingWizard: () => null }))
vi.mock('@/hooks/useEnterpriseId', () => ({ useEnterpriseId: () => 'enterprise-1' }))
vi.mock('@/hooks/useLiveCompany', () => ({ useLiveCompany: mocks.live }))
vi.mock('@/hooks/useDashboardOverview', () => ({ useDashboardOverview: mocks.overview }))
vi.mock('react-router-dom', () => ({ Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a> }))

function renderPage() {
  return renderToStaticMarkup(<BossDashboard />)
}

describe('BossDashboard', () => {
  beforeEach(() => {
    mocks.live.mockReturnValue({
      vitals: {
        agents: { production: 3, total: 3, training: 0, recruit: 0 },
        health: { tone: 'alive' }, shadow: { match: 81 },
        collected_at: '2026-09-30T08:00:00Z',
      },
      events: [], loading: false, refreshing: false, error: null,
      mode: 'poll', lastUpdated: Date.now(), refresh: vi.fn(),
    })
    mocks.overview.mockReturnValue({
      orgMetrics: null, runtime: null, rosterCount: 3, deciding: null,
      approvals: Array.from({ length: 6 }, (_, i) => ({
        id: `approval-${i}`, enterprise_id: 'enterprise-1',
        title: `审批标题 ${i}`, description: `完整审批说明 ${i}`,
        requester_name: `申请人 ${i}`, requester_id: `requester-${i}`,
        created_at: '2026-09-30T08:00:00Z',
      })),
      trend: [], trendAvailable: true, refreshSecondary: vi.fn(), decide: vi.fn(),
    })
  })

  it('renders every approval with its description and both actions', () => {
    const html = renderPage()
    expect(html).toContain('审批标题 5')
    expect(html).toContain('完整审批说明 5')
    expect(html).toContain('requester-5')
    expect(html.match(/>批准</g)).toHaveLength(6)
    expect(html.match(/>驳回</g)).toHaveLength(6)
  })

  it('exposes an in-progress background refresh and describes polling accurately', () => {
    mocks.live.mockReturnValue({ ...mocks.live(), refreshing: true })
    const html = renderPage()
    expect(html).toContain('aria-busy="true"')
    expect(html).toContain('刷新中…')
    expect(html).toContain('事件与指标每 15 秒轮询')
  })
})
