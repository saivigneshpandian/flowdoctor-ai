"use client";

import { ArrowUp, Loader2, Mic } from "lucide-react";
import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type Ref } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export interface ChatInputHandle {
  focus: () => void;
}

interface ChatInputProps {
  ref?: Ref<ChatInputHandle>;
  busy: boolean;
  placeholder: string;
  // Resolves to false when the message did not go through, so the draft can be restored.
  onSend: (message: string) => Promise<boolean>;
}

export function ChatInput({ ref, busy, placeholder, onSend }: ChatInputProps) {
  const [draft, setDraft] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({ focus: () => textarea.current?.focus() }), []);

  // Dictation tools type into whatever has focus, so the box is ready as soon as the page opens.
  useEffect(() => {
    textarea.current?.focus();
  }, []);

  const canSend = draft.trim().length > 0 && !busy;

  async function submit() {
    if (!canSend) return;
    const message = draft.trim();
    setDraft("");
    const delivered = await onSend(message);
    if (!delivered) setDraft((current) => current || message);
    textarea.current?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <div className="rounded-xl border border-input bg-panel-raised shadow-[0_12px_40px_-16px_rgb(0_0_0/70%)] transition-colors focus-within:border-ring/60">
      <Textarea
        ref={textarea}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        rows={3}
        aria-label="Message the FlowDoctor assistant"
        placeholder={placeholder}
        className="max-h-56 min-h-24 resize-none rounded-xl border-0 bg-transparent px-4 pt-3.5 text-base shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center justify-between gap-3 px-3 pb-3 pl-4">
        <p className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <Mic aria-hidden className="size-3.5 shrink-0" />
          <span>Hold your Wispr key and speak</span>
          <span className="hidden font-mono text-[11px] opacity-70 md:inline">
            · Enter to send · Shift+Enter for new line
          </span>
        </p>
        <Button
          type="button"
          disabled={!canSend}
          onClick={() => void submit()}
          className="h-9 gap-1.5 px-3.5"
        >
          {busy ? (
            <Loader2 aria-hidden className="size-4 animate-spin" />
          ) : (
            <ArrowUp aria-hidden className="size-4" />
          )}
          {busy ? "Working" : "Send"}
        </Button>
      </div>
    </div>
  );
}
