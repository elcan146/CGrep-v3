import * as React from 'react';
import { cn } from '@/lib/utils';

const Textarea = React.forwardRef(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      'flex w-full rounded-md border border-[rgba(255,255,255,0.10)] bg-[rgba(255,255,255,0.05)] px-3 py-2 text-sm text-white placeholder:text-[rgba(255,255,255,0.3)] focus:outline-none focus:border-[rgba(0,229,255,0.40)] disabled:cursor-not-allowed disabled:opacity-50 resize-none colorScheme-dark',
      className,
    )}
    {...props}
  />
));
Textarea.displayName = 'Textarea';

export { Textarea };
