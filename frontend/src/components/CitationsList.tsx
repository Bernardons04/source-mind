"use client";

import React, { useState } from "react";
import { Citation } from "@/lib/api";

interface CitationsListProps {
  citations: Citation[];
}

export function CitationsList({ citations }: CitationsListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!citations || citations.length === 0) return null;

  return (
    <div className="source-box">
      <div style={{ fontWeight: 650, fontSize: "11px", marginBottom: "6px", display: "flex", alignItems: "center", gap: "6px" }}>
        <span>▤</span>
        <span>Sources & Citations ({citations.length})</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
        {citations.map((c, idx) => {
          const isExpanded = expandedId === c.id;
          const pageInfo = c.metadata?.page ? ` · Page ${c.metadata.page}` : "";
          const docTitle = c.document_name || "Document";

          return (
            <div
              key={c.id || idx}
              style={{
                background: "var(--panel-2)",
                border: "1px solid var(--border)",
                borderRadius: "8px",
                padding: "8px 10px",
                fontSize: "11px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  cursor: "pointer",
                }}
                onClick={() => setExpandedId(isExpanded ? null : c.id)}
              >
                <div style={{ fontWeight: 600, color: "var(--text)", display: "flex", alignItems: "center", gap: "6px" }}>
                  <span style={{ color: "var(--accent)" }}>[{idx + 1}]</span>
                  <span>{docTitle}</span>
                  <span style={{ color: "var(--muted)", fontWeight: 400 }}>{pageInfo}</span>
                </div>
                <span style={{ color: "var(--muted)", fontSize: "12px" }}>
                  {isExpanded ? "▴" : "▾"}
                </span>
              </div>

              {isExpanded && (
                <div
                  style={{
                    marginTop: "6px",
                    paddingTop: "6px",
                    borderTop: "1px dashed var(--border)",
                    color: "var(--soft)",
                    fontStyle: "italic",
                    lineHeight: "1.5",
                  }}
                >
                  "{c.quote}"
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
