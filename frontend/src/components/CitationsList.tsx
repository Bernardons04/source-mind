"use client";

import React, { useState } from "react";
import { Citation } from "@/lib/api";
import { useApp } from "@/lib/context";
import { ChevronRight, FileText } from "lucide-react";

interface CitationsListProps {
  citations: Citation[];
}

export function CitationsList({ citations }: CitationsListProps) {
  const { t } = useApp();
  const [isMainExpanded, setIsMainExpanded] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!citations || citations.length === 0) return null;

  return (
    <div className="source-box">
      {/* Accordion Principal "Sources" */}
      <button
        type="button"
        className="source-main-header"
        onClick={() => setIsMainExpanded(!isMainExpanded)}
        aria-expanded={isMainExpanded}
      >
        <span className="source-main-title">
          <ChevronRight
            size={13}
            strokeWidth={2.2}
            className={`source-chevron ${isMainExpanded ? "expanded" : ""}`}
          />
          <FileText size={12} strokeWidth={2} style={{ opacity: 0.8 }} />
          <span>{t("chat.citationsTitle")}</span>
        </span>
        <span className="source-count-badge">
          {citations.length}
        </span>
      </button>

      {/* Conteúdo com os accordions individuais das fontes */}
      {isMainExpanded && (
        <div className="source-items-wrapper">
          {citations.map((c, idx) => {
            const isExpanded = expandedId === c.id;
            const pageInfo = c.metadata?.page ? ` · p. ${c.metadata.page}` : "";
            const docTitle = c.document_name || "Document";

            return (
              <div key={c.id || idx} className="source-item">
                <div
                  className="source-item-header"
                  onClick={() => setExpandedId(isExpanded ? null : c.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setExpandedId(isExpanded ? null : c.id);
                    }
                  }}
                >
                  <div className="source-item-title">
                    <span className="source-item-idx">[{idx + 1}]</span>
                    <span className="source-item-name" title={docTitle}>
                      {docTitle}
                    </span>
                    {pageInfo && <span className="source-item-page">{pageInfo}</span>}
                  </div>
                  <ChevronRight
                    size={11}
                    strokeWidth={2}
                    className={`source-chevron-item ${isExpanded ? "expanded" : ""}`}
                  />
                </div>

                {isExpanded && (
                  <div className="source-item-snippet">
                    <blockquote className="source-snippet-quote">
                      "{c.quote}"
                    </blockquote>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
