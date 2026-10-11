"use client";

import { Extension } from "@tiptap/core";
import { Mention as MentionExtension } from "@tiptap/extension-mention";
import Placeholder from "@tiptap/extension-placeholder";
import { EditorContent, ReactRenderer, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { ArrowUpIcon, Loader2, PlusIcon, Paperclip } from "lucide-react";

import React, {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import tippy from "tippy.js";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupText,
} from "@/components/ui/input-group";
import { cn } from "@/lib/utils";

// ── Types / helpers ──────────────────────────────────────────────────────────

export function createMentionConfig(config) {
  return config;
}

// ── Context ──────────────────────────────────────────────────────────────────

const ChatInputContext = createContext({
  mentionConfigs: [],
  addMentionConfig: () => {},
  onSubmit: () => {},
  onStop: undefined,
  isStreaming: false,
  disabled: false,
  value: undefined,
  onChange: undefined,
  editor: null,
  setEditor: () => {},
});

// ── ChatInput (root) ─────────────────────────────────────────────────────────

export function ChatInput({
  children,
  className,
  onSubmit,
  isStreaming = false,
  onStop,
  disabled = false,
  value,
  onChange,
  ...props
}) {
  const [mentionConfigs, setMentionConfigs] = useState([]);
  const [editor, setEditor] = useState(null);
  const registeredTypesRef = useRef(new Set());

  const addMentionConfig = useCallback((config) => {
    if (registeredTypesRef.current.has(config.type)) {
      setMentionConfigs((prev) => {
        const idx = prev.findIndex((c) => c.type === config.type);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = config;
          return updated;
        }
        return prev;
      });
    } else {
      registeredTypesRef.current.add(config.type);
      setMentionConfigs((prev) => [...prev, config]);
    }
  }, []);

  return (
    <ChatInputContext.Provider
      value={{
        mentionConfigs,
        addMentionConfig,
        onSubmit,
        onStop,
        isStreaming,
        disabled,
        value,
        onChange,
        editor,
        setEditor,
      }}
    >
      <InputGroup
        className={cn(
          "focus-within:ring-1 focus-within:ring-ring",
          className,
        )}
        {...props}
      >
        {children}
      </InputGroup>
    </ChatInputContext.Provider>
  );
}

// ── ChatInputEditor ──────────────────────────────────────────────────────────

export function ChatInputEditor({
  disabled,
  onEnter,
  placeholder = "Type a message...",
  className,
  value,
  onChange,
}) {
  const {
    mentionConfigs,
    onSubmit,
    disabled: contextDisabled,
    value: contextValue,
    onChange: contextOnChange,
    setEditor,
  } = useContext(ChatInputContext);

  const effectiveValue = value ?? contextValue;
  const effectiveOnChange = onChange ?? contextOnChange;
  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const onEnterRef = useRef(onEnter || onSubmit);
  useEffect(() => {
    onEnterRef.current = onEnter || onSubmit;
  }, [onEnter, onSubmit]);

  const extensions = useMemo(
    () => [
      StarterKit,
      Placeholder.configure({ placeholder }),
      KeyboardShortcuts.configure({
        getOnEnter: () => onEnterRef.current,
      }),
      ...mentionConfigs.map((config) => {
        const MentionPlugin = MentionExtension.extend({
          name: `${config.type}-mention`,
        });
        return MentionPlugin.configure({
          HTMLAttributes: {
            class: cn(
              "bg-[rgba(0,229,255,0.2)] text-[#00e5ff] rounded-sm px-1 py-0.5 no-underline",
              config.editorMentionClass,
            ),
          },
          suggestion: {
            char: config.trigger,
            ...getMentionSuggestion(config),
          },
        });
      }),
    ],
    [mentionConfigs, placeholder],
  );

  const onUpdate = useCallback(
    ({ editor }) => {
      if (isMounted) {
        effectiveOnChange?.(editor.getJSON());
      }
    },
    [effectiveOnChange, isMounted],
  );

  const editor = useEditor(
    {
      extensions,
      content: effectiveValue,
      onUpdate,
      editable: !(disabled || contextDisabled),
      immediatelyRender: false,
    },
    [extensions, disabled, contextDisabled],
  );

  useEffect(() => {
    if (editor) setEditor(editor);
    return () => setEditor(null);
  }, [editor, setEditor]);

  useEffect(() => {
    if (
      effectiveValue &&
      editor &&
      JSON.stringify(effectiveValue) !== JSON.stringify(editor.getJSON())
    ) {
      editor.commands.setContent(effectiveValue);
    }
  }, [effectiveValue, editor]);

  return (
    <>
      <style>{`
        .tiptap:focus { outline: none; }
        .tiptap p.is-editor-empty:first-child::before {
          color: var(--color-muted-foreground, rgba(255,255,255,0.4));
          content: attr(data-placeholder);
          float: left;
          height: 0;
          pointer-events: none;
        }
        .tiptap p { margin: 0; }
      `}</style>
      <EditorContent
        editor={editor}
        className={cn(
          "w-full h-full max-h-48 min-h-[40px] px-3 pt-3 pb-1 overflow-y-auto text-sm text-foreground",
          className,
        )}
      />
    </>
  );
}

// ── Keyboard shortcuts extension ─────────────────────────────────────────────

const KeyboardShortcuts = Extension.create({
  addKeyboardShortcuts() {
    return {
      Enter: () => {
        const onEnter = this.options.getOnEnter?.();
        if (onEnter) {
          onEnter();
        }
        return true;
      },
    };
  },
  addOptions() {
    return {
      getOnEnter: () => () => {},
    };
  },
});

// ── ChatInputMention ─────────────────────────────────────────────────────────

export function ChatInputMention({
  type,
  trigger,
  items,
  children,
  editorMentionClass,
}) {
  const { addMentionConfig } = useContext(ChatInputContext);

  const renderItemRef = useRef(children);
  useEffect(() => {
    renderItemRef.current = children;
  }, [children]);

  useEffect(() => {
    addMentionConfig({
      type,
      trigger,
      items,
      renderItem: renderItemRef.current,
      editorMentionClass,
    });
  }, [addMentionConfig, type, trigger, items, editorMentionClass]);

  return null;
}

// ── Mention list popup ───────────────────────────────────────────────────────

const GenericMentionList = forwardRef((props, ref) => {
  const { items, command, renderItem } = props;
  const [selectedIndex, setSelectedIndex] = useState(0);
  const itemRefs = useRef([]);

  const selectItem = useCallback(
    (index) => {
      const item = items[index];
      if (item) {
        command({ id: item.id, label: item.name });
      }
    },
    [items, command],
  );

  const scrollToItem = useCallback((index) => {
    const el = itemRefs.current[index];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  const upHandler = useCallback(() => {
    setSelectedIndex((prev) => {
      const n = (prev + items.length - 1) % items.length;
      scrollToItem(n);
      return n;
    });
  }, [items.length, scrollToItem]);

  const downHandler = useCallback(() => {
    setSelectedIndex((prev) => {
      const n = (prev + 1) % items.length;
      scrollToItem(n);
      return n;
    });
  }, [items.length, scrollToItem]);

  const enterHandler = useCallback(() => {
    selectItem(selectedIndex);
  }, [selectedIndex, selectItem]);

  useEffect(() => {
    setSelectedIndex(0);
    itemRefs.current = itemRefs.current.slice(0, items.length);
  }, [items]);

  const handleKeyDown = useCallback(
    (event) => {
      if (event.key === "ArrowUp") { upHandler(); return true; }
      if (event.key === "ArrowDown") { downHandler(); return true; }
      if (event.key === "Enter") { enterHandler(); return true; }
      return false;
    },
    [upHandler, downHandler, enterHandler],
  );

  useImperativeHandle(ref, () => ({ handleKeyDown }), [handleKeyDown]);

  return (
    <div className="min-w-48 max-w-64 max-h-48 bg-[rgba(15,18,28,0.97)] text-white border border-[rgba(255,255,255,0.10)] rounded-lg shadow-xl flex flex-col gap-1 overflow-y-auto p-1">
      {items.length ? (
        items.map((item, index) => (
          <Button
            key={item.id}
            variant="ghost"
            size="sm"
            className={cn(
              "flex justify-start px-1 py-2 gap-2",
              selectedIndex === index && "bg-[rgba(255,255,255,0.10)]",
            )}
            onClick={() => selectItem(index)}
            ref={(el) => { if (el) itemRefs.current[index] = el; }}
          >
            {renderItem ? (
              renderItem(item, selectedIndex === index)
            ) : (
              <span className="px-2">{item.name}</span>
            )}
          </Button>
        ))
      ) : (
        <div className="text-sm text-[rgba(255,255,255,0.4)] px-2 py-1.5">
          No results found
        </div>
      )}
    </div>
  );
});

// ── Mention suggestion factory ───────────────────────────────────────────────

function getMentionSuggestion(config) {
  return {
    items: ({ query }) => {
      return config.items.filter((item) =>
        item.name.toLowerCase().startsWith(query.toLowerCase()),
      );
    },
    render: () => {
      let component;
      let popup;

      return {
        onStart: (props) => {
          component = new ReactRenderer(GenericMentionList, {
            props: {
              items: props.items,
              command: props.command,
              renderItem: config.renderItem,
            },
            editor: props.editor,
          });

          if (!props.clientRect) return;

          popup = tippy(document.body, {
            getReferenceClientRect: props.clientRect,
            appendTo: () => document.body,
            content: component.element,
            showOnCreate: true,
            interactive: true,
            trigger: "manual",
            placement: "bottom-start",
          });
        },
        onUpdate: (props) => {
          component.updateProps(props);
          if (!props.clientRect) return;
          popup.setProps({ getReferenceClientRect: props.clientRect });
        },
        onKeyDown: (props) => {
          if (props.event.key === "Escape") {
            popup.hide();
            return true;
          }
          return component.ref?.handleKeyDown?.(props.event) || false;
        },
        onExit: () => {
          popup.destroy();
          component.destroy();
        },
      };
    },
  };
}

// ── ChatInputSubmitButton ────────────────────────────────────────────────────

export function ChatInputSubmitButton({
  className,
  isStreaming,
  onStop,
  disabled,
  ...props
}) {
  const {
    onSubmit,
    onStop: onStopContext,
    isStreaming: isStreamingContext,
    disabled: contextDisabled,
  } = useContext(ChatInputContext);

  const loading = isStreaming ?? isStreamingContext;
  const effectiveOnStop = onStop ?? onStopContext;
  const effectiveDisabled = disabled ?? contextDisabled;
  const isStopVariant = loading && effectiveOnStop;
  const isLoadingVariant = loading && !effectiveOnStop;
  const handleClick = isStopVariant ? effectiveOnStop : onSubmit;

  if (isStopVariant) {
    return (
      <InputGroupButton
        variant="default"
        size="icon-sm"
        className={cn("rounded-full", className)}
        onClick={handleClick}
        disabled={effectiveDisabled}
        {...props}
      >
        <StopIcon className="h-3.5 w-3.5" />
        <span className="sr-only">Stop</span>
      </InputGroupButton>
    );
  }

  if (isLoadingVariant) {
    return (
      <InputGroupButton
        variant="default"
        size="icon-sm"
        className={cn("rounded-full", className)}
        onClick={handleClick}
        disabled={effectiveDisabled}
        {...props}
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span className="sr-only">Loading</span>
      </InputGroupButton>
    );
  }

  return (
    <InputGroupButton
      variant="default"
      size="icon-sm"
      className={cn("rounded-full", className)}
      onClick={handleClick}
      disabled={effectiveDisabled}
      {...props}
    >
      <ArrowUpIcon className="h-3.5 w-3.5" />
      <span className="sr-only">Send</span>
    </InputGroupButton>
  );
}

const StopIcon = ({ className }) => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 16 16"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <title>Stop</title>
    <rect x="2" y="2" width="12" height="12" rx="2" fill="currentColor" />
  </svg>
);

// ── ChatInputMentionButton ───────────────────────────────────────────────────

export function ChatInputMentionButton({ className, ...props }) {
  const { mentionConfigs, editor } = useContext(ChatInputContext);

  if (!mentionConfigs.length) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <InputGroupButton
          variant="outline"
          size="icon-sm"
          className={cn("rounded-full shrink-0", className)}
          {...props}
        >
          <PlusIcon className="h-4 w-4" />
          <span className="sr-only">Add Mention</span>
        </InputGroupButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-48">
        {mentionConfigs.map((config) => (
          <DropdownMenuItem
            key={config.type}
            onClick={() => {
              if (editor) {
                editor.commands.insertContent(config.trigger);
                editor.commands.focus();
              }
            }}
          >
            {config.trigger} {config.type}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ── ChatInputFileButton (attach, no implementation yet) ──────────────────────

export function ChatInputFileButton({ className, onClick, ...props }) {
  return (
    <InputGroupButton
      variant="outline"
      size="icon-xs"
      className={cn("rounded-full shrink-0", className)}
      onClick={onClick}
      {...props}
    >
      <PlusIcon className="h-3.5 w-3.5" />
      <span className="sr-only">Attach file</span>
    </InputGroupButton>
  );
}

// ── ChatInputGroupAddon / Button / Text re-exports ───────────────────────────

export function ChatInputGroupAddon({ className, ...props }) {
  return <InputGroupAddon className={cn(className)} {...props} />;
}

export function ChatInputGroupButton({ className, ...props }) {
  return <InputGroupButton className={cn(className)} {...props} />;
}

export function ChatInputGroupText({ className, ...props }) {
  return <InputGroupText className={cn(className)} {...props} />;
}

// ── useChatInput hook ────────────────────────────────────────────────────────

export function useChatInput({ mentions, initialValue, onSubmit: onCustomSubmit }) {
  const [value, setValue] = useState(
    initialValue ?? { type: "doc", content: [] },
  );

  const configsArray = useMemo(
    () => (mentions ? Object.values(mentions) : []),
    [mentions],
  );

  const parsed = useMemo(
    () => parseContent(value, configsArray),
    [value, configsArray],
  );

  const clear = useCallback(() => {
    setValue({ type: "doc", content: [] });
  }, []);

  const handleSubmit = useCallback(() => {
    if (parsed.content.trim().length === 0) return;
    if (onCustomSubmit) onCustomSubmit(parsed);
    clear();
  }, [parsed, onCustomSubmit, clear]);

  return {
    value,
    onChange: setValue,
    parsed,
    clear,
    handleSubmit,
    ...(mentions ? { mentionConfigs: mentions } : {}),
  };
}

// ── parseContent ─────────────────────────────────────────────────────────────

export function parseContent(json, configs) {
  let content = "";
  const mentions = {};

  function recurse(node) {
    if (node.type === "text" && node.text) {
      content += node.text;
    } else if (node.type === "hardBreak") {
      content += "\n";
    } else if (node.type?.endsWith("-mention")) {
      const mentionType = node.type.slice(0, -8);
      const config = configs.find((c) => c.type === mentionType);
      if (config) {
        const attrs = node.attrs ?? {};
        const id = attrs.id;
        const label = attrs.label;
        content += `${config.trigger}${label}`;
        if (!mentions[mentionType]) mentions[mentionType] = [];
        const item = config.items.find((i) => i.id === id);
        if (item && !mentions[mentionType].some((ex) => ex.id === id)) {
          mentions[mentionType].push(item);
        }
      } else {
        content += node.text ?? "";
      }
    } else if (node.content) {
      for (const child of node.content) recurse(child);
      if (node.type === "paragraph") content += "\n\n";
    }
  }

  if (json.content) {
    for (const node of json.content) recurse(node);
  }

  content = content.trim();
  return { content, ...mentions };
}
