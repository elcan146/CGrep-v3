import * as React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

const InputGroup = React.forwardRef(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      'flex flex-col w-full border border-border bg-background rounded-xl shadow-sm',
      className,
    )}
    {...props}
  >
    {children}
  </div>
));
InputGroup.displayName = 'InputGroup';

const InputGroupAddon = React.forwardRef(({ className, align, children, ...props }, ref) => {
  const alignClass =
    align === 'block-end'
      ? 'flex w-full items-center gap-2 px-2 pb-2 pt-0.5'
      : 'flex w-full items-center gap-2 px-2 py-1.5';
  return (
    <div ref={ref} className={cn(alignClass, className)} {...props}>
      {children}
    </div>
  );
});
InputGroupAddon.displayName = 'InputGroupAddon';

const InputGroupButton = React.forwardRef(({ className, variant = 'default', size = 'default', children, ...props }, ref) => {
  const sizes = {
    default: 'h-8 px-3',
    sm: 'h-7 px-2 text-xs',
    'icon-sm': 'h-7 w-7',
    'icon-xs': 'h-6 w-6',
  };
  return (
    <Button
      ref={ref}
      variant={variant}
      className={cn(sizes[size] ?? sizes.default, className)}
      {...props}
    >
      {children}
    </Button>
  );
});
InputGroupButton.displayName = 'InputGroupButton';

const InputGroupText = React.forwardRef(({ className, children, ...props }, ref) => (
  <span
    ref={ref}
    className={cn('flex items-center px-2 text-xs text-[rgba(255,255,255,0.5)]', className)}
    {...props}
  >
    {children}
  </span>
));
InputGroupText.displayName = 'InputGroupText';

export { InputGroup, InputGroupAddon, InputGroupButton, InputGroupText };
