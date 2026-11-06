"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { api, Assistant, DocumentItem } from "@/lib/api";
import { KnowledgeBaseView } from "@/components/KnowledgeBaseView";
import { Loader2 } from "lucide-react";

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
      <div className="page-spinner">
        <Loader2 size={28} strokeWidth={2} className="animate-spin" style={{ color: "var(--accent)" }} />
        <span>Loading knowledge base...</span>
      </div>
    );
  }

  if (!assistant) return null;

  return <KnowledgeBaseView assistant={assistant} initialDocuments={documents} />;
}
