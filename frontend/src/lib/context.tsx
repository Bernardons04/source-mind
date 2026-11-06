"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { Language, translate } from "./i18n";
import { createClient } from "./supabase/client";
import { api } from "./api";

interface AppContextType {
  theme: "dark" | "light";
  toggleTheme: () => void;
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  user: {
    id?: string;
    email?: string;
    name?: string;
    initials?: string;
  };
  signOut: () => Promise<void>;
  showToast: (msg: string) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;
}

const AppContext = createContext<AppContextType | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<"dark" | "light">("dark");
  const [language, setLanguageState] = useState<Language>("en");
  const [user, setUser] = useState<{ id?: string; email?: string; name?: string; initials?: string }>({
    email: "you@example.com",
    name: "Your account",
    initials: "YA",
  });
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window !== "undefined") {
      return window.innerWidth > 800;
    }
    return true;
  });

  const toggleSidebar = () => setSidebarOpen((prev) => !prev);

  // Initialize theme, language & user session
  useEffect(() => {
    // 1. Theme
    const savedTheme = (localStorage.getItem("manualmind_theme") as "dark" | "light") || "dark";
    setThemeState(savedTheme);
    applyThemeToDoc(savedTheme);

    // 2. Language
    const savedLang = (localStorage.getItem("manualmind_lang") as Language) || "en";
    setLanguageState(savedLang);

    // 3. User session from Supabase Auth
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session?.user) {
        const email = session.user.email || "you@example.com";
        const name = session.user.user_metadata?.full_name || email.split("@")[0];
        const initials = name
          .split(" ")
          .map((n: string) => n[0])
          .join("")
          .toUpperCase()
          .slice(0, 2);
        setUser({ id: session.user.id, email, name, initials });
      }
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (session?.user) {
        const email = session.user.email || "you@example.com";
        const name = session.user.user_metadata?.full_name || email.split("@")[0];
        const initials = name
          .split(" ")
          .map((n: string) => n[0])
          .join("")
          .toUpperCase()
          .slice(0, 2);
        setUser({ id: session.user.id, email, name, initials });
      } else {
        setUser({ email: "you@example.com", name: "Your account", initials: "YA" });
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  function applyThemeToDoc(t: "dark" | "light") {
    document.documentElement.setAttribute("data-theme", t);
    if (t === "light") {
      document.documentElement.classList.add("light");
      document.documentElement.style.colorScheme = "light";
    } else {
      document.documentElement.classList.remove("light");
      document.documentElement.style.colorScheme = "dark";
    }
  }

  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setThemeState(nextTheme);
    localStorage.setItem("manualmind_theme", nextTheme);
    applyThemeToDoc(nextTheme);
    api.updatePreferences({ theme: nextTheme }).catch(() => {});
    showToast(`${nextTheme === "dark" ? "Dark" : "Light"} mode enabled`);
  };

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem("manualmind_lang", lang);
    api.updatePreferences({ language: lang }).catch(() => {});
    showToast(`Language set to ${lang.toUpperCase()}`);
  };

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage((curr) => (curr === msg ? null : curr));
    }, 2800);
  };

  const signOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    localStorage.removeItem("dev_auth_token");
    window.location.href = "/login";
  };

  const t = (key: string, params?: Record<string, string | number>) => {
    return translate(language, key, params);
  };

  return (
    <AppContext.Provider
      value={{
        theme,
        toggleTheme,
        language,
        setLanguage,
        t,
        user,
        signOut,
        showToast,
        sidebarOpen,
        setSidebarOpen,
        toggleSidebar,
      }}
    >
      {children}
      {toastMessage && (
        <div className="toast" role="status">
          {toastMessage}
        </div>
      )}
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}
