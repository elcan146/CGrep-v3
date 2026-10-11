import * as React from 'react';
import { cn } from '@/lib/utils';

const Button = React.forwardRef(
  ({ className, variant = 'default', size = 'default', ...props }, ref) => {
    const base =
      'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus:outline-none disabled:opacity-50 disabled:pointer-events-none';
    const variants = {
      default: 'bg-foreground text-background hover:opacity-90',
      secondary: 'bg-[rgba(0,229,255,0.15)] text-[#00e5ff] border border-[rgba(0,229,255,0.30)] hover:bg-[rgba(0,229,255,0.25)]',
      ghost:   'hover:bg-muted text-muted-foreground',
      outline: 'border border-border bg-transparent hover:bg-muted text-muted-foreground',
    };
    const sizes = {
      default: 'h-9 px-4 py-2',
      sm:      'h-7 px-3 text-xs',
      icon:    'h-8 w-8',
      'icon-sm': 'h-7 w-7',
      'icon-xs': 'h-6 w-6',
    };
    return (
      <button
        ref={ref}
        className={cn(base, variants[variant] ?? variants.default, sizes[size] ?? sizes.default, className)}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { Button };
