"use client";

import React, { useState } from "react";
import { MessageEvent } from "@/lib/api";

interface ExecutionEventsProps {
  events: MessageEvent[];
}

export function ExecutionEvents({ events }: ExecutionEventsProps) {
  const [isOpen, setIsOpen] = useState(false);

  if (!events || events.length === 0) return null;

  return (
    <div style={{ marginTop: "6px" }}>
      <button
        type="button"
        className="events-toggle"
        onClick={() => setIsOpen(!isOpen)}
        title="Inspect LangGraph RAG execution trace"
      >
        <span>⚙</span>
        <span>Execution Trace ({events.length} steps)</span>
        <span>{isOpen ? "▴" : "▾"}</span>
      </button>

      {isOpen && (
        <div className="events-box">
          {events.map((ev) => (
            <div
              key={ev.id || ev.sequence_number}
              style={{
                marginBottom: "8px",
                paddingBottom: "8px",
                borderBottom: "1px solid #22262c",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", color: "#a4d883", fontWeight: 600 }}>
                <span>
                  #{ev.sequence_number} {ev.event_type}
                </span>
                <span style={{ fontSize: "10px", color: "var(--muted)" }}>
                  {new Date(ev.created_at).toLocaleTimeString()}
                </span>
              </div>
              <pre
                style={{
                  margin: "4px 0 0",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  fontSize: "10.5px",
                  color: "#94a3b8",
                }}
              >
                {JSON.stringify(ev.payload, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
