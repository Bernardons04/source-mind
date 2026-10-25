"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { api, Assistant } from "@/lib/api";
import { ChatInterface } from "@/components/ChatInterface";

export default function NewChatPage() {
  const params = useParams();
  const slug = params?.slug as string;
  const [assistant, setAssistant] = useState<Assistant | null>(null);

  useEffect(() => {
    if (slug) {
      api.getAssistant(slug)
        .then(setAssistant)
        .catch(console.error);
    }
  }, [slug]);

  if (!assistant) return null;

  return <ChatInterface assistant={assistant} />;
}
