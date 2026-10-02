import { ButtonHTMLAttributes, forwardRef } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'outline' | 'danger'
type Size = 'sm' | 'md' | 'lg' | 'icon'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

const variantClasses: Record<Variant, string> = {
  primary: 'bg-[#1F4FD8] text-white hover:bg-[#1a44be] shadow-[0_1px_2px_rgba(0,0,0,0.06)] active:scale-[0.98]',
  secondary: 'bg-white text-[#0B0B0B] border border-[#E4E4E1] hover:bg-[#FAFAF9] shadow-[0_1px_2px_rgba(0,0,0,0.04)] active:scale-[0.98]',
  ghost: 'text-[#6B6B66] hover:bg-[#F4F4F3] hover:text-[#0B0B0B] active:scale-[0.98]',
  outline: 'border border-[#E4E4E1] bg-transparent text-[#0B0B0B] hover:bg-[#F4F4F3] active:scale-[0.98]',
  danger: 'bg-[#B23A2F] text-white hover:bg-[#9a2f26] shadow-[0_1px_2px_rgba(0,0,0,0.06)] active:scale-[0.98]',
}

const sizeClasses: Record<Size, string> = {
  sm: 'h-8 px-3 text-[12px] rounded-[6px] gap-1.5',
  md: 'h-9 px-4 text-[13px] rounded-[6px] gap-2',
  lg: 'h-11 px-5 text-[14px] rounded-[6px] gap-2.5',
  icon: 'h-9 w-9 rounded-[6px]',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className = '', children, ...props }, ref) => {
    return (
      <button
        ref={ref}
                className={`inline-flex items-center justify-center whitespace-nowrap font-semibold transition-[transform,background-color,border-color,color,box-shadow] duration-200 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-canvas)] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}

        {...props}
      >
        {children}
      </button>
    )
  }
)

Button.displayName = 'Button'
