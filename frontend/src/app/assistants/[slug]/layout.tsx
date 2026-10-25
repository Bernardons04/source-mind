"use client";

import React, { useState, useEffect } from "react";
import { useParams, usePathname } from "next/navigation";
import { api, Assistant } from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";
import { Topbar } from "@/components/Topbar";

export default function AssistantLayout({ children }: { children: React.ReactNode }) {
  const params = useParams();
  const pathname = usePathname();
  const slug = params?.slug as string;

  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (slug) {
      setLoading(true);
      api.getAssistant(slug)
        .then(setAssistant)
        .catch((err) => console.error("Error loading assistant:", err))
        .finally(() => setLoading(false));
    }
  }, [slug]);

  const isKnowledge = pathname.includes("/knowledge");
  const activeView = isKnowledge ? "knowledge" : "chat";

  const breadcrumbs = [
    { label: "Workspace", href: "/assistants" },
    { label: assistant?.name || slug, href: `/assistants/${slug}/chat` },
    { label: isKnowledge ? "Knowledge Base" : "Chat" },
  ];

  return (
    <div className="app-container">
      <Sidebar currentAssistant={assistant} activeView={activeView} />

      <main className="main">
        <Topbar breadcrumbs={breadcrumbs} />
        <section className="content" style={{ padding: isKnowledge ? "36px 42px 56px" : "20px 32px 30px" }}>
          {loading ? (
            <div style={{ padding: "60px 0", textAlign: "center", color: "var(--muted)" }}>
              Loading assistant...
            </div>
          ) : assistant ? (
            children
          ) : (
            <div style={{ padding: "60px 0", textAlign: "center", color: "#f87171" }}>
              Assistant not found.
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
