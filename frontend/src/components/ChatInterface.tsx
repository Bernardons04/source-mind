"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { api, Assistant, Message } from "@/lib/api";
import { CitationsList } from "./CitationsList";
import { ExecutionEvents } from "./ExecutionEvents";

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
      // If no chat session created yet, create one
      if (!activeChatId) {
        const newChat = await api.createChat(assistant.slug, question.slice(0, 45));
        activeChatId = newChat.id;
        setChatId(newChat.id);
        // Update URL quietly without full reload
        window.history.pushState(null, "", `/assistants/${assistant.slug}/chat/${newChat.id}`);
      }

      // Send to backend RAG LangGraph workflow
      const assistantResponse = await api.sendMessage(assistant.slug, activeChatId, question);
      setMessages((prev) => [...prev, assistantResponse]);
    } catch (err: any) {
      showToast(err.message || "Failed to generate answer");
      const errorMsg: Message = {
        id: "err-" + Date.now(),
        chat_id: chatId || "",
        role: "assistant",
        content: `Error: ${err.message || "Could not generate response. Please check assistant OpenRouter key or connection."}`,
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

  const assistantInitial = assistant.name.charAt(0).toUpperCase();
  const showWelcome = messages.length === 0;

  return (
    <div className="chat-content">
      {/* Topline */}
      <div className="chat-topline">
        <div className="chat-assistant">
          <div className="card-symbol">{assistantInitial}</div>
          <div>
            <strong>{assistant.name}</strong>
            <small>{t("chat.groundedNotice")}</small>
          </div>
        </div>

        <Link
          href={`/assistants/${assistant.slug}/knowledge`}
          className="quiet-button"
          id="chatKnowledge"
          style={{ textDecoration: "none" }}
        >
          <span>▤</span>
          <span>{t("chat.knowledgeButton")}</span>
        </Link>
      </div>

      {/* Conversation */}
      <div className="conversation">
        {showWelcome && (
          <>
            <div className="welcome" id="welcomeBlock">
              <div className="welcome-mark">✳</div>
              <h2>{t("chat.welcomeTitle")}</h2>
              <p>{t("chat.welcomeSubtitle")}</p>
            </div>

            <div className="suggestions" id="suggestions">
              <button
                type="button"
                className="suggestion"
                onClick={() => handleSendMessage(t("chat.suggestion1"))}
              >
                {t("chat.suggestion1")}
                <span>{t("chat.suggestion1Sub")}</span>
              </button>
              <button
                type="button"
                className="suggestion"
                onClick={() => handleSendMessage(t("chat.suggestion2"))}
              >
                {t("chat.suggestion2")}
                <span>{t("chat.suggestion2Sub")}</span>
              </button>
              <button
                type="button"
                className="suggestion"
                onClick={() => handleSendMessage(t("chat.suggestion3"))}
              >
                {t("chat.suggestion3")}
                <span>{t("chat.suggestion3Sub")}</span>
              </button>
              <button
                type="button"
                className="suggestion"
                onClick={() => handleSendMessage(t("chat.suggestion4"))}
              >
                {t("chat.suggestion4")}
                <span>{t("chat.suggestion4Sub")}</span>
              </button>
            </div>
          </>
        )}

        {/* Message Stream */}
        <div className="messages" id="messages">
          {messages.map((m) => (
            <div
              key={m.id}
              className={`message ${m.role}`}
            >
              <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>

              {/* Citations */}
              {m.citations && m.citations.length > 0 && (
                <CitationsList citations={m.citations} />
              )}

              {/* LangGraph Execution Events */}
              {m.events && m.events.length > 0 && (
                <ExecutionEvents events={m.events} />
              )}
            </div>
          ))}

          {/* Loading indicator */}
          {isLoading && (
            <div className="message assistant" style={{ fontStyle: "italic", color: "var(--muted)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span className="animate-spin">⧗</span>
                <span>{t("chat.sending")}</span>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Composer */}
      <div className="composer-wrap">
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
            ↑
          </button>
        </form>
        <p className="disclaimer">{t("chat.disclaimer")}</p>
      </div>
    </div>
  );
}
