"use client";

import React, { useState, useEffect, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { api, Assistant, Message } from "@/lib/api";
import { CitationsList } from "./CitationsList";
import { ArrowUp, Loader2, Sparkles } from "lucide-react";
import MarkdownIt from "markdown-it";

// Singleton markdown renderer
const md = new MarkdownIt({
  html: false,        // disable raw HTML injection (XSS safety)
  linkify: true,
  typographer: true,
  breaks: true,       // \n → <br>
});

interface ChatInterfaceProps {
  assistant: Assistant;
  initialChatId?: string;
  initialMessages?: Message[];
}

export function ChatInterface({
  assistant,
  initialChatId,
  initialMessages = [],
}: ChatInterfaceProps) {
  const { t, showToast } = useApp();
  const router = useRouter();

  const [chatId, setChatId] = useState<string | null>(initialChatId || null);
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Adjust textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 140)}px`;
    }
  }, [input]);

  const handleSendMessage = async (textToSend?: string) => {
    const question = (textToSend || input).trim();
    if (!question || isLoading) return;

    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    // Optimistic user message
    const tempUserMsg: Message = {
      id: "temp-user-" + Date.now(),
      chat_id: chatId || "",
      role: "user",
      content: question,
      created_at: new Date().toISOString(),
      citations: [],
      events: [],
    };
    setMessages((prev) => [...prev, tempUserMsg]);
    setIsLoading(true);

    try {
      let activeChatId = chatId;
      if (!activeChatId) {
        const newChat = await api.createChat(assistant.slug, question.slice(0, 45));
        activeChatId = newChat.id;
        setChatId(newChat.id);
        window.history.pushState(null, "", `/assistants/${assistant.slug}/chat/${newChat.id}`);
      }

      const assistantResponse = await api.sendMessage(assistant.slug, activeChatId, question);
      setMessages((prev) => [...prev, assistantResponse]);
    } catch (err: any) {
      showToast(err.message || "Failed to generate answer");
      const errorMsg: Message = {
        id: "err-" + Date.now(),
        chat_id: chatId || "",
        role: "assistant",
        content: `**Error:** ${err.message || "Could not generate response. Please check assistant OpenRouter key or connection."}`,
        created_at: new Date().toISOString(),
        citations: [],
        events: [],
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const showWelcome = messages.length === 0;

  return (
    <div className="chat-shell">
      {/* Scrollable conversation area */}
      <div className="conversation" id="conversation">
        <div className="chat-content">
          {showWelcome && (
            <div className="welcome" id="welcomeBlock">
              <div className="welcome-mark">
                <Sparkles size={28} strokeWidth={1.5} />
              </div>
              <h2>{t("chat.welcomeTitle")}</h2>
              <p>{t("chat.welcomeSubtitle")}</p>
            </div>
          )}

          {/* Message Stream */}
          <div className="messages" id="messages">
            {messages.map((m) => (
              <div key={m.id} className={`message ${m.role}`}>
                {m.role === "assistant" ? (
                  <>
                    {m.citations && m.citations.length > 0 && (
                      <CitationsList citations={m.citations} />
                    )}
                    <div
                      className="markdown-body"
                      dangerouslySetInnerHTML={{ __html: md.render(m.content) }}
                    />
                  </>
                ) : (
                  <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                )}
              </div>
            ))}

            {/* Loading indicator */}
            {isLoading && (
              <div className="message assistant" style={{ fontStyle: "italic", color: "var(--muted)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <Loader2 size={15} strokeWidth={2} className="animate-spin" />
                  <span>{t("chat.sending")}</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>
      </div>

      {/* Sticky composer — always at the bottom */}
      <div className="composer-wrap">
        <div className="chat-content">
          <form
            className="composer"
            id="chatForm"
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
          >
            <textarea
              ref={textareaRef}
              id="chatInput"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t("chat.placeholder", { assistant: assistant.name })}
              aria-label="Message"
              disabled={isLoading}
            />
            <button
              type="submit"
              className="send-button"
              disabled={isLoading || !input.trim()}
              aria-label="Send message"
            >
              <ArrowUp size={17} strokeWidth={2.5} />
            </button>
          </form>
          <p className="disclaimer">{t("chat.disclaimer")}</p>
        </div>
      </div>
    </div>
  );
}
