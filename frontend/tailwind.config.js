/** @type {import('tailwindcss').Config} */
export default {
  // v4「深空仪表」：不使用全局 dark: 变体，改用 .zone-command 分区语义变量
  // （见 src/index.css §v4 深空仪表）。组件写 bg-[var(--bg-surface)] 即可自动适配两区。
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 背景 - 暖白内容区 + 冷灰导航区（冷暖分区区分"操作"与"阅读"）
        canvas: '#FAFAF7',      // 内容区暖白
        surface: '#FFFFFF',     // 卡片白
        elevated: '#F5F4EF',    // 次级层级暖灰
        'surface-2': '#F5F4EF',
        'surface-3': '#EFEEE8',
        'nav-bg': '#F8F9FB',    // 导航区冷灰（V3.1 设计令牌）
        // 边框（暖灰）
        'border-subtle': 'rgba(30, 58, 95, 0.06)',
        'border-default': '#E5E3DC',
        'border-strong': '#D9D6CC',
        // 文字
        'text-primary': '#1A1A1A',
        'text-secondary': '#525252',
        'text-tertiary': '#6B6B6B',
        'text-muted': '#8A8A8A',
        'text-disabled': '#A3A3A3',
        // 品牌色 - 深海军蓝（保持不变）
        brand: {
          50: '#F0F4F9',
          100: '#DCE5F0',
          200: '#B6C4D6',
          300: '#8FA5BC',
          400: '#6B8CB1',
          500: '#1E3A5F',
          600: '#1A3358',
          700: '#15293F',
          800: '#101F30',
          900: '#0A1520',
        },
        // 交互蓝 - V3.1 新增，用于按钮/链接/交互元素
        'interactive': {
          50: '#E3F0FF',
          100: '#BAD6FF',
          200: '#7FB3FF',
          300: '#4D94FF',
          400: '#2E7DFF',
          500: '#1A6CFF',
          600: '#0D5BE6',
          700: '#0A47B8',
        },
        // 语义色
        success: '#16A34A',
        warning: '#D97706',
        error: '#DC2626',
        info: '#2563EB',
        // v4 深空仪表 —— 指挥区底座（.zone-command 内生效）
        ink: {
          900: '#0A0E13',
          800: '#0F141C',
          700: '#151C26',
          600: '#1D2733',
          500: '#28343F',
        },
        // v4 信号色 —— 仪表状态语义（青=健康/蓝=进行/琥珀=待办/红=故障/灰=待机）
        sig: {
          alive: '#4DD8C0',
          focus: '#4C8DFF',
          alert: '#F5A524',
          fault: '#FF5D5D',
          idle: '#64748B',
        },
        // 分区自适应语义 token：组件用这些类即可跨两区通用
        zone: {
          canvas: 'var(--bg-canvas)',
          surface: 'var(--bg-surface)',
          elevated: 'var(--bg-elevated)',
          muted: 'var(--bg-muted)',
          border: 'var(--border-default)',
          'border-subtle': 'var(--border-subtle)',
          text: 'var(--text-primary)',
          'text-2': 'var(--text-secondary)',
          'text-3': 'var(--text-tertiary)',
          'text-muted': 'var(--text-muted)',
          brand: 'var(--brand)',
        },
        // AutoTeams v2「克制 · 编辑式」令牌（autoteams_ui/DESIGN.md §2）
        // 纸面白底 + 发丝线分隔 + 单一主色，映射 --at-* 变量，与 v1 令牌共存。
        'at-paper': 'var(--at-paper)',
        'at-card': 'var(--at-card)',
        'at-alt': 'var(--at-alt)',
        'at-hairline': 'var(--at-hairline)',
        'at-ink': 'var(--at-ink)',
        'at-muted': 'var(--at-ink-muted)',
        'at-subtle': 'var(--at-ink-subtle)',
        'at-primary': 'var(--at-primary)',
        'at-on-primary': 'var(--at-on-primary)',
        'at-positive': 'var(--at-positive)',
        'at-caution': 'var(--at-caution)',
        'at-blocked': 'var(--at-blocked)',
      },
      fontSize: {
        'display': ['48px', { lineHeight: '1.1', letterSpacing: '-0.02em', fontWeight: '700' }],
        'h1': ['36px', { lineHeight: '1.2', letterSpacing: '-0.01em', fontWeight: '700' }],
        'h2': ['28px', { lineHeight: '1.3', letterSpacing: '-0.005em', fontWeight: '600' }],
        'h3': ['20px', { lineHeight: '1.4', fontWeight: '600' }],
        'h4': ['17px', { lineHeight: '1.4', fontWeight: '600' }],  // V3.1 新增
        'body': ['15px', { lineHeight: '1.5', fontWeight: '400' }],
        'body-sm': ['13px', { lineHeight: '1.5', fontWeight: '400' }], // V3.1 新增
        'caption': ['12px', { lineHeight: '1.4', letterSpacing: '0.01em', fontWeight: '500' }],
      },
      spacing: {
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '5': '24px',
        '6': '32px',
        '7': '48px',
        '8': '64px',
      },
      borderRadius: {
        'sm': '8px',
        'md': '12px',
        'lg': '16px',
        'xl': '20px',
        '2xl': '24px',
        'full': '9999px',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        serif: ['"Source Serif 4"', '"Noto Serif SC"', 'Georgia', 'serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      backgroundImage: {
        // 海军蓝品牌渐变（登录/注册左栏品牌叙事）
        'brand-gradient': 'linear-gradient(135deg, #1E3A5F 0%, #15293F 100%)',
        'brand-gradient-soft': 'linear-gradient(135deg, rgba(30,58,95,0.07) 0%, rgba(30,58,95,0.04) 100%)',
      },
      boxShadow: {
        'soft': '0 1px 2px rgba(30,58,95,0.04), 0 4px 16px rgba(30,58,95,0.05)',
        'lift': '0 2px 4px rgba(30,58,95,0.05), 0 12px 32px rgba(30,58,95,0.08)',
        'focus': '0 0 0 3px rgba(30,58,95,0.12)',
      },
      animation: {
        'fade-in': 'fadeIn 0.2s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'scale-in': 'scaleIn 0.15s ease-out',
        'shimmer': 'shimmer 2s linear infinite',
        // 方案C 工作流节点连线流动
        'flow-dash': 'flowDash 1.2s linear infinite',
        // 方案A 部署流水线阶段脉冲
        'pipeline-pulse': 'pipelinePulse 2.4s ease-out infinite',
        // 工作流节点执行脉冲
        'wf-node-active': 'wfNodeActive 2.4s ease-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.97)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        // 工作流虚线流动
        flowDash: {
          'to': { strokeDashoffset: '-24' },
        },
        // 流水线阶段脉冲
        pipelinePulse: {
          '0%': { boxShadow: '0 0 0 0 rgba(30,58,95,0.30)' },
          '70%': { boxShadow: '0 0 0 8px rgba(30,58,95,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(30,58,95,0)' },
        },
        // 工作流节点激活脉冲
        wfNodeActive: {
          '0%': { boxShadow: '0 0 0 0 rgba(30,58,95,0.30)' },
          '70%': { boxShadow: '0 0 0 10px rgba(30,58,95,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(30,58,95,0)' },
        },
      },
    },
  },
  plugins: [],
}
