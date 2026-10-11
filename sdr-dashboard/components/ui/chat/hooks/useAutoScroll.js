import { useRef, useEffect, useCallback, useState } from 'react';

export function useAutoScroll({ smooth = false, content } = {}) {
  const scrollRef = useRef(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);
  const userScrolledRef = useRef(false);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    setIsAtBottom(true);
    setAutoScrollEnabled(true);
    userScrolledRef.current = false;
  }, [smooth]);

  const disableAutoScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom > 40) {
      userScrolledRef.current = true;
      setAutoScrollEnabled(false);
    }
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      const atBottom = dist < 40;
      setIsAtBottom(atBottom);
      if (atBottom) {
        setAutoScrollEnabled(true);
        userScrolledRef.current = false;
      }
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (autoScrollEnabled) scrollToBottom();
  }, [content, autoScrollEnabled, scrollToBottom]);

  return { scrollRef, isAtBottom, autoScrollEnabled, scrollToBottom, disableAutoScroll };
}
