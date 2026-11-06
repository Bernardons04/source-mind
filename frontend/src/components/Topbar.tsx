"use client";

import React from "react";
import Link from "next/link";
import { useApp } from "@/lib/context";
import { PanelLeft } from "lucide-react";

interface TopbarProps {
  breadcrumbs?: { label: string; href?: string }[];
}

export function Topbar({ breadcrumbs = [{ label: "Workspace" }, { label: "Assistants" }] }: TopbarProps) {
  const { toggleSidebar, sidebarOpen } = useApp();

  return (
    <header className="topbar">
      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <button
          type="button"
          className="sidebar-toggle-btn"
          id="toggleSidebarBtn"
          onClick={toggleSidebar}
          aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
          title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
        >
          <PanelLeft size={18} strokeWidth={2} />
        </button>

        <div className="crumb">
          {breadcrumbs.map((crumb, idx) => (
            <React.Fragment key={idx}>
              {idx > 0 && <span className="crumb-separator">/</span>}
              {crumb.href ? (
                <Link href={crumb.href} className="crumb-link">
                  {crumb.label}
                </Link>
              ) : (
                <span className="crumb-current">{crumb.label}</span>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>
    </header>
  );
}
