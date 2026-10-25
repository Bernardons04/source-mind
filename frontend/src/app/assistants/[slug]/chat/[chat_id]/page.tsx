"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { api, Assistant, Message } from "@/lib/api";
import { ChatInterface } from "@/components/ChatInterface";

export default function ExistingChatPage() {
  const params = useParams();
  const slug = params?.slug as string;
  const chatId = params?.chat_id as string;

  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (slug && chatId) {
      setLoading(true);
      Promise.all([
        api.getAssistant(slug),
        api.getChatMessages(slug, chatId),
      ])
        .then(([ast, msgs]) => {
          setAssistant(ast);
          setMessages(msgs);
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [slug, chatId]);

  if (loading) {
    return (
      <div style={{ padding: "60px 0", textAlign: "center", color: "var(--muted)" }}>
        Loading chat history...
      </div>
    );
  }

  if (!assistant) return null;

  return (
    <ChatInterface
      assistant={assistant}
      initialChatId={chatId}
      initialMessages={messages}
    />
  );
}
