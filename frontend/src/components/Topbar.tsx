"use client";

import React from "react";
import Link from "next/link";
import { useApp } from "@/lib/context";

interface TopbarProps {
  breadcrumbs?: { label: string; href?: string }[];
}

export function Topbar({ breadcrumbs = [{ label: "Workspace" }, { label: "Assistants" }] }: TopbarProps) {
  const { theme, toggleTheme } = useApp();

  return (
    <header className="topbar">
      <div className="crumb">
        {breadcrumbs.map((crumb, idx) => (
          <React.Fragment key={idx}>
            {idx > 0 && <span style={{ color: "#555b65", margin: "0 6px" }}>/</span>}
            {crumb.href ? (
              <Link href={crumb.href} style={{ color: "inherit", textDecoration: "none" }}>
                {crumb.label}
              </Link>
            ) : (
              <span>{crumb.label}</span>
            )}
          </React.Fragment>
        ))}
      </div>

      <div className="top-actions">
        <button
          type="button"
          className="quiet-button"
          id="themeQuick"
          onClick={toggleTheme}
          title="Toggle light/dark appearance"
        >
          <span>◐</span>
          <span id="quickThemeText">{theme === "dark" ? "Dark" : "Light"}</span>
        </button>
      </div>
    </header>
  );
}
