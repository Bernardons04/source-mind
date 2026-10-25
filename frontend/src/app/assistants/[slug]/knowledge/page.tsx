"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { api, Assistant, DocumentItem } from "@/lib/api";
import { KnowledgeBaseView } from "@/components/KnowledgeBaseView";

export default function KnowledgePage() {
  const params = useParams();
  const slug = params?.slug as string;

  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (slug) {
      setLoading(true);
      Promise.all([
        api.getAssistant(slug),
        api.getDocuments(slug),
      ])
        .then(([ast, docs]) => {
          setAssistant(ast);
          setDocuments(docs);
        })
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [slug]);

  if (loading) {
    return (
      <div style={{ padding: "60px 0", textAlign: "center", color: "var(--muted)" }}>
        Loading knowledge base documents...
      </div>
    );
  }

  if (!assistant) return null;

  return <KnowledgeBaseView assistant={assistant} initialDocuments={documents} />;
}
