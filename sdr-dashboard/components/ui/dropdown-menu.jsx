import * as React from 'react';
import { cn } from '@/lib/utils';

const DropdownMenuContext = React.createContext({ open: false, setOpen: () => {} });

function DropdownMenu({ children }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <DropdownMenuContext.Provider value={{ open, setOpen }}>
      <div ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
        {children}
      </div>
    </DropdownMenuContext.Provider>
  );
}

const DropdownMenuTrigger = React.forwardRef(({ asChild, children, ...props }, ref) => {
  const { open, setOpen } = React.useContext(DropdownMenuContext);
  const toggle = () => setOpen(!open);

  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(children, { ...props, ref, onClick: toggle });
  }
  return (
    <button ref={ref} onClick={toggle} {...props}>
      {children}
    </button>
  );
});
DropdownMenuTrigger.displayName = 'DropdownMenuTrigger';

function DropdownMenuContent({ className, align = 'start', children, ...props }) {
  const { open, setOpen } = React.useContext(DropdownMenuContext);
  if (!open) return null;

  const alignStyle = align === 'end' ? { right: 0 } : { left: 0 };

  return (
    <div
      className={cn(
        'absolute z-50 bottom-full mb-1 min-w-[10rem] bg-[rgba(15,18,28,0.97)] border border-[rgba(255,255,255,0.10)] rounded-lg shadow-xl py-1',
        className,
      )}
      style={alignStyle}
      {...props}
    >
      {React.Children.map(children, (child) =>
        React.isValidElement(child) ? React.cloneElement(child, { _close: () => setOpen(false) }) : child,
      )}
    </div>
  );
}

function DropdownMenuItem({ className, onClick, _close, children, ...props }) {
  return (
    <button
      className={cn(
        'w-full text-left px-3 py-1.5 text-xs text-[rgba(255,255,255,0.75)] hover:bg-[rgba(255,255,255,0.08)] cursor-pointer transition-colors',
        className,
      )}
      onClick={(e) => {
        onClick?.(e);
        _close?.();
      }}
      {...props}
    >
      {children}
    </button>
  );
}

export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem };
