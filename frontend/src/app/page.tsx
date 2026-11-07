"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useApp } from "@/lib/context";
import { Language } from "@/lib/i18n";
import "@/styles/landing.css";

declare global {
  interface Window {
    gsap?: any;
    ScrollTrigger?: any;
  }
}

export default function LandingPage() {
  const { t, theme, toggleTheme, language, setLanguage, user, signOut } = useApp();
  const [langMenuOpen, setLangMenuOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const langRef = useRef<HTMLDivElement>(null);
  const userRef = useRef<HTMLDivElement>(null);

  // Close dropdown menus on click outside or escape
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (langRef.current && !langRef.current.contains(e.target as Node)) {
        setLangMenuOpen(false);
      }
      if (userRef.current && !userRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setLangMenuOpen(false);
        setUserMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  // GSAP and ScrollTrigger Animation Setup
  useEffect(() => {
    let ctx: any;
    let cleanupScrollTriggers = () => { };

    const setupAnimations = async () => {
      try {
        const { gsap } = await import("gsap");
        const { ScrollTrigger } = await import("gsap/ScrollTrigger");
        gsap.registerPlugin(ScrollTrigger);

        const rm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const narrow = window.matchMedia("(max-width:900px)").matches;

        const scene = document.querySelector("#scene") as HTMLElement | null;
        const pin = document.querySelector("#pin") as HTMLElement | null;
        const flyer = document.querySelector("#flyer") as HTMLElement | null;
        const vi = document.querySelector("#vi") as HTMLElement | null;
        const list = document.querySelector(".list") as HTMLElement | null;
        const copy = document.querySelector(".copy") as HTMLElement | null;

        const P = [
          document.querySelector(".s-l") as HTMLElement | null,
          document.querySelector(".s-c") as HTMLElement | null,
          document.querySelector(".s-r") as HTMLElement | null,
          document.querySelector(".s-m") as HTMLElement | null,
        ].filter(Boolean) as HTMLElement[];

        const its = Array.from(document.querySelectorAll<HTMLButtonElement>(".it"));

        let cur = 0;
        let auto = true;
        let tabsOn = false;

        const go = (i: number) => {
          cur = i;
          its.forEach((b, k) => {
            b.classList.toggle("on", k === i);
            b.setAttribute("aria-selected", String(k === i));
            b.tabIndex = k === i ? 0 : -1;
          });
          P.forEach((p, k) => {
            p.classList.toggle("act", k === i);
            if (tabsOn) p.setAttribute("aria-hidden", String(k !== i));
          });
        };

        const stopAuto = () => {
          auto = false;
          list?.classList.add("stop");
        };

        const setTabs = (on: boolean) => {
          if (on === tabsOn) return;
          tabsOn = on;
          flyer?.classList.toggle("tabs-on", on);
          pin?.classList.toggle("ready", on);
          if (on) {
            its.forEach((b) => b.classList.remove("on"));
            if (list) void list.offsetWidth;
            go(0);
          } else {
            P.forEach((p) => {
              p.removeAttribute("aria-hidden");
              p.classList.remove("act");
            });
          }
        };

        its.forEach((b, k) => {
          b.addEventListener("click", () => {
            stopAuto();
            go(k);
          });
        });

        const handleListKeyDown = (e: KeyboardEvent) => {
          if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
          e.preventDefault();
          stopAuto();
          const n = (cur + (e.key === "ArrowDown" ? 1 : its.length - 1)) % its.length;
          go(n);
          its[n]?.focus();
        };

        const handleAnimationEnd = (e: AnimationEvent) => {
          if (auto && tabsOn && e.animationName === "fill") {
            go((cur + 1) % its.length);
          }
        };

        list?.addEventListener("keydown", handleListKeyDown);
        list?.addEventListener("animationend", handleAnimationEnd);

        // IntersectionObserver for reveal elements (.rvv)
        const rv = document.querySelectorAll(".rvv");
        if ("IntersectionObserver" in window && !rm) {
          const io = new IntersectionObserver(
            (entries) => {
              entries.forEach((e) => {
                if (e.isIntersecting) {
                  e.target.classList.add("in");
                  io.unobserve(e.target);
                }
              });
            },
            { threshold: 0.35 }
          );
          rv.forEach((el) => io.observe(el));
        } else {
          rv.forEach((el) => el.classList.add("in"));
        }

        const staticMode = () => {
          document.documentElement.classList.add("static");
          if (vi && flyer) vi.appendChild(flyer);
          P.forEach((p) => {
            p.style.cssText = "";
          });
          setTabs(true);
          if (rm) stopAuto();
          const cvx = document.querySelector(".cv");
          if (cvx) cvx.classList.add("in");
        };

        if (rm || narrow || !scene || !pin || !flyer || !vi || P.length < 4) {
          staticMode();
          return;
        }

        ctx = gsap.context(() => {
          // --- Hero 3D Convergence Animation ---
          const g: Record<string, any> = {};
          const ring = document.querySelector(".ring");
          const hx = document.querySelectorAll(".hx");

          const calc = () => {
            if (!pin || !vi || !flyer || !P[1]) return;
            const pr = pin.getBoundingClientRect();
            const vr = vi.getBoundingClientRect();
            const c = P[1];
            const cW = c.offsetWidth;
            const cH = c.offsetHeight;
            const ox = c.offsetLeft + cW / 2;
            const oy = c.offsetTop + cH / 2;
            const cX = flyer.offsetLeft + ox;
            const cY = flyer.offsetTop + oy;
            g.S = vr.width / cW;
            g.fx = vr.left - pr.left + vr.width / 2 - cX;
            g.fy = vr.top - pr.top + vr.height / 2 - cY;
            g.ym = pin.offsetHeight / 2 + 24 - cY;
            [0, 2].forEach((k) => {
              const p = P[k];
              g[k] = {
                tx: ox - (p.offsetLeft + p.offsetWidth / 2),
                ty: oy - (p.offsetTop + p.offsetHeight / 2),
                s: cW / p.offsetWidth,
              };
            });
            gsap.set(flyer, { transformOrigin: `${ox}px ${oy}px` });
          };

          gsap.set(P[0], { rotateY: 32 });
          gsap.set(P[2], { rotateY: -32 });
          gsap.set(P[1], { rotateX: 4 });
          calc();

          ScrollTrigger.addEventListener("refreshInit", calc);

          const E = "power3.inOut";
          const tl = gsap.timeline({
            defaults: { ease: "none" },
            scrollTrigger: {
              trigger: scene,
              start: "top top",
              end: "bottom bottom",
              scrub: 0.6,
              invalidateOnRefresh: true,
              onUpdate: (s: any) => {
                pin?.classList.toggle("moving", s.progress > 0.005);
                if (copy) copy.style.pointerEvents = s.progress > 0.2 ? "none" : "";
                setTabs(s.progress > 0.86);
              },
            },
          });

          tl.to(copy, { y: -100, opacity: 0, duration: 2.2 }, 0.2)
            .to(hx, { opacity: 0, duration: 1.2 }, 0.1)
            .to(ring, { rotation: 220, duration: 10 }, 0)
            .to(flyer, { y: () => g.ym, duration: 3.4, ease: "power2.inOut" }, 0.4)
            .to(P[0], { x: () => g[0]?.tx, y: () => g[0]?.ty, scale: () => g[0]?.s, rotateY: 0, duration: 3, ease: E }, 1.2)
            .to(P[1], { rotateX: 0, x: 26, y: -22, scale: 0.96, duration: 3, ease: E }, 1.2)
            .to(P[2], { x: () => (g[2]?.tx || 0) + 52, y: () => (g[2]?.ty || 0) - 44, scale: () => (g[2]?.s || 1) * 0.92, rotateY: 0, duration: 3, ease: E }, 1.2)
            .set(P[0], { zIndex: 4 }, 2.4)
            .fromTo(flyer, { x: 0, y: () => g.ym, scale: 1 }, { x: () => g.fx, y: () => g.fy, scale: () => g.S, duration: 3.2, ease: E, immediateRender: false }, 4.2)
            .to(P[1], { x: 0, y: 0, scale: 1, duration: 2, ease: "power2.inOut" }, 5.4)
            .to(P[2], { x: () => g[2]?.tx, y: () => g[2]?.ty, scale: () => g[2]?.s, duration: 2, ease: "power2.inOut" }, 5.4)
            .fromTo(".how .view", { opacity: 0 }, { opacity: 1, duration: 1.2 }, 5)
            .fromTo(".how h2", { opacity: 0 }, { opacity: 1, duration: 1.2 }, 6.2)
            .fromTo(".how .list", { opacity: 0 }, { opacity: 1, duration: 1.2 }, 6.6)
            .to({}, { duration: 2 }, 8);

          // --- Problem vs Solution Interactive Animation ---
          const sec = document.querySelector("#problema");
          if (sec) {
            const qt = document.querySelector(".qt");
            const cards = gsap.utils.toArray(".fcd");
            const hls = gsap.utils.toArray(".fcd .hl");
            const bd = gsap.utils.toArray(".bdg");
            const xs = gsap.utils.toArray(".fcd.x");
            const nt = document.querySelector(".nt");
            const ac = document.querySelector(".ac2");
            const s1 = document.querySelector(".s1");
            const s2 = document.querySelector(".s2");
            const sr = document.querySelector(".sr");
            const c1 = document.querySelector(".c1");
            const c2 = document.querySelector(".c2");
            const c3 = document.querySelector(".c3");
            const pp = document.querySelector(".pp");
            const sp = document.querySelector(".sp");
            const o = { n: 0 };

            const typ = () => {
              const currentQ = qt?.getAttribute("data-q") || "Quantos dias de férias eu tenho?";
              if (qt) qt.textContent = currentQ.slice(0, Math.round(o.n));
            };
            typ();

            const jit = [
              [-10, 8, -3],
              [12, -6, 3],
              [-8, -10, 2],
              [10, 10, -3],
            ];
            cards.forEach((c: any, i: number) => {
              gsap.set(c, { x: jit[i][0], y: jit[i][1], rotation: jit[i][2], scale: 0.96, opacity: 0.8 });
            });
            gsap.set(hls, { "--hs": 0, color: "#8b9099" });
            gsap.set(bd, { scale: 0, opacity: 0 });
            gsap.set(nt, { opacity: 0 });
            gsap.set([ac, s1, s2, sr], { opacity: 0 });
            gsap.set(ac, { y: 24 });
            gsap.set(sp, { opacity: 0.2, y: 16 });
            gsap.set(c1, { opacity: 1 });
            gsap.set([c2, c3], { opacity: 0 });

            const tlProb = gsap.timeline({
              defaults: { ease: "none" },
              scrollTrigger: {
                trigger: sec,
                start: "top top",
                end: "bottom bottom",
                scrub: 0.6,
                invalidateOnRefresh: true,
              },
            });

            const getQLen = () => (qt?.getAttribute("data-q") || "Quantos dias de férias eu tenho?").length;

            tlProb
              .to(o, { n: getQLen, duration: 1.6, onUpdate: typ }, 0.2)
              .to(cards, { x: 0, y: 0, rotation: 0, scale: 1, opacity: 1, duration: 1.6, ease: "power2.out", stagger: 0.12 }, 1.8)
              .to(c1, { opacity: 0, duration: 0.6 }, 2)
              .to(c2, { opacity: 1, duration: 0.6 }, 2.2)
              .to(hls, { "--hs": 100, color: "#101113", duration: 1, ease: "power1.inOut", stagger: 0.6 }, 3.6)
              .to(bd, { scale: 1, opacity: 1, duration: 0.5, ease: "back.out(2)", stagger: 0.6 }, 4)
              .to(xs, { opacity: 0.35, duration: 1 }, 4.4)
              .to(nt, { opacity: 1, duration: 0.8 }, 5)
              .to(c2, { opacity: 0, duration: 0.6 }, 5.4)
              .to(c3, { opacity: 1, duration: 0.6 }, 5.6)
              .to(ac, { opacity: 1, y: 0, duration: 1.2, ease: "power2.out" }, 5.4)
              .to(s1, { opacity: 1, duration: 0.8 }, 6)
              .to(s2, { opacity: 1, duration: 0.8 }, 6.7)
              .to(sr, { opacity: 1, duration: 0.8 }, 7.3)
              .to(pp, { opacity: 0.3, duration: 1.5 }, 5)
              .to(sp, { opacity: 1, y: 0, duration: 1.8 }, 5.8)
              .to({}, { duration: 2 }, 8.4);
          }

          ScrollTrigger.refresh();
        });

        cleanupScrollTriggers = () => {
          ScrollTrigger.getAll().forEach((t: any) => t.kill());
          list?.removeEventListener("keydown", handleListKeyDown);
          list?.removeEventListener("animationend", handleAnimationEnd);
        };
      } catch (err) {
        console.error("Failed to initialize GSAP landing page animations:", err);
      }
    };

    setupAnimations();

    return () => {
      if (ctx) ctx.revert();
      cleanupScrollTriggers();
    };
  }, []);

  // Smooth scroll handler for anchor links
  const handleScrollToHow = (e: React.MouseEvent) => {
    const scene = document.querySelector("#scene") as HTMLElement | null;
    if (scene) {
      e.preventDefault();
      const rm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({
        top: scene.offsetTop + scene.offsetHeight - window.innerHeight,
        behavior: rm ? "auto" : "smooth",
      });
    }
  };

  const ctaHref = user?.id ? "/assistants" : "/login";
  const ctaText = user?.id ? t("landing.nav.workspace") : t("landing.nav.cta");

  const getLanguageLabel = (l: Language) => {
    switch (l) {
      case "pt":
        return "Português";
      case "es":
        return "Español";
      case "en":
      default:
        return "English";
    }
  };

  return (
    <>
      <header>
        <div className="w nav">
          <Link className="brand" href="/">
            <b>S</b>SourceMind
          </Link>
          <a className="l" href="#como" onClick={handleScrollToHow}>
            {t("landing.nav.how")}
          </a>
          <a className="l" href="#confianca">
            {t("landing.nav.confidence")}
          </a>

          <div className="hr">
            {/* Language Switcher */}
            <div className="lang" id="lang" ref={langRef}>
              <button
                className="lg-btn"
                id="lgb"
                type="button"
                aria-haspopup="menu"
                aria-expanded={langMenuOpen}
                aria-controls="lgm"
                aria-label={t("user.language")}
                onClick={() => setLangMenuOpen((prev) => !prev)}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" />
                </svg>
                <span className="lg-cur">{getLanguageLabel(language)}</span>
                <svg className="chev" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M4 6l4 4 4-4" />
                </svg>
              </button>
              <div className="lg-menu" id="lgm" role="menu" aria-label={t("user.language")} hidden={!langMenuOpen}>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={language === "en"}
                  onClick={() => {
                    setLanguage("en");
                    setLangMenuOpen(false);
                  }}
                >
                  <span className="cd">EN</span>English
                  <svg className="ck" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M3 8.5l3 3 7-7" />
                  </svg>
                </button>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={language === "pt"}
                  onClick={() => {
                    setLanguage("pt");
                    setLangMenuOpen(false);
                  }}
                >
                  <span className="cd">PT</span>Português
                  <svg className="ck" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M3 8.5l3 3 7-7" />
                  </svg>
                </button>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={language === "es"}
                  onClick={() => {
                    setLanguage("es");
                    setLangMenuOpen(false);
                  }}
                >
                  <span className="cd">ES</span>Español
                  <svg className="ck" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M3 8.5l3 3 7-7" />
                  </svg>
                </button>
              </div>
            </div>

            {/* Theme Toggle */}
            <button
              className="tgl"
              id="tgl"
              type="button"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? t("user.lightMode") : t("user.darkMode")}
              title={theme === "dark" ? t("user.lightMode") : t("user.darkMode")}
            >
              {theme === "dark" ? (
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
                </svg>
              ) : (
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
                </svg>
              )}
            </button>

            {/* Auth CTA or User Menu */}
            {user?.id ? (
              <div className="usr-dropdown-wrapper" ref={userRef}>
                <button
                  type="button"
                  className="usr-btn"
                  aria-haspopup="menu"
                  aria-expanded={userMenuOpen}
                  onClick={() => setUserMenuOpen((prev) => !prev)}
                >
                  <span className="usr-avatar">{user.initials || "YA"}</span>
                  <span className="usr-name">{user.name || t("landing.nav.accountFallback")}</span>
                  <svg className="chev" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M4 6l4 4 4-4" />
                  </svg>
                </button>
                <div className="usr-menu" role="menu" hidden={!userMenuOpen}>
                  <div className="usr-menu-header">
                    <span className="u-name">{user.name || t("landing.nav.accountFallback")}</span>
                    <span className="u-email">{user.email || "you@example.com"}</span>
                  </div>
                  <Link href="/assistants" role="menuitem" onClick={() => setUserMenuOpen(false)}>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <rect x="3" y="3" width="7" height="7"></rect>
                      <rect x="14" y="3" width="7" height="7"></rect>
                      <rect x="14" y="14" width="7" height="7"></rect>
                      <rect x="3" y="14" width="7" height="7"></rect>
                    </svg>
                    {t("landing.nav.menuWorkspace")}
                  </Link>
                  <div style={{ height: "1px", background: "var(--line)", margin: "4px 0" }} />
                  <button
                    type="button"
                    className="logout"
                    role="menuitem"
                    onClick={() => {
                      setUserMenuOpen(false);
                      signOut();
                    }}
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                      <polyline points="16 17 21 12 16 7"></polyline>
                      <line x1="21" y1="12" x2="9" y2="12"></line>
                    </svg>
                    {t("landing.nav.menuLogout")}
                  </button>
                </div>
              </div>
            ) : (
              <Link className="btn s" href="/login">
                {t("landing.nav.cta")}
              </Link>
            )}
          </div>
        </div>
      </header>

      <main>
        {/* Hero & Convergence Scene */}
        <div id="scene" className="scene">
          <div id="pin" className="pin hero">
            <video className="bgvid" muted loop playsInline autoPlay preload="none" aria-hidden="true">
              {/* Optional background video */}
            </video>
            <i className="ray r1"></i>
            <i className="ray r2"></i>
            <span className="rw" aria-hidden="true">
              <i className="ring"></i>
            </span>

            <div className="copy w hc">
              <span className="badge">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M3 8.5l3 3 7-7" />
                </svg>
                {t("landing.hero.badge")}
              </span>
              <h1>
                {t("landing.hero.titleBefore")}
                <em>{t("landing.hero.titleHighlight")}</em>
              </h1>
              <p className="lead">{t("landing.hero.lead")}</p>
              <div className="acts">
                <Link className="pb" href={ctaHref}>
                  {t("landing.hero.ctaPrimary")}
                  <i>
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M3 8h10M9 4l4 4-4 4" />
                    </svg>
                  </i>
                </Link>
                <a className="pg" href="#como" onClick={handleScrollToHow}>
                  <i>
                    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
                      <path d="M3 1.5v9l7-4.5z" />
                    </svg>
                  </i>
                  {t("landing.hero.ctaSecondary")}
                </a>
              </div>
            </div>

            {/* How It Works Tabs View */}
            <div className="how" id="como">
              <div className="w">
                <h2 className="c">{t("landing.how.title")}</h2>
                <div className="tabs2">
                  <div className="list" role="tablist" aria-label="Passos">
                    <button className="it on" role="tab" id="tb0" aria-controls="tp0" aria-selected="true">
                      <i className="bar"></i>
                      <b>{t("landing.how.step1Title")}</b>
                      <span>{t("landing.how.step1Desc")}</span>
                    </button>
                    <button className="it" role="tab" id="tb1" aria-controls="tp1" aria-selected="false" tabIndex={-1}>
                      <i className="bar"></i>
                      <b>{t("landing.how.step2Title")}</b>
                      <span>{t("landing.how.step2Desc")}</span>
                    </button>
                    <button className="it" role="tab" id="tb2" aria-controls="tp2" aria-selected="false" tabIndex={-1}>
                      <i className="bar"></i>
                      <b>{t("landing.how.step3Title")}</b>
                      <span>{t("landing.how.step3Desc")}</span>
                    </button>
                    <button className="it" role="tab" id="tb3" aria-controls="tp3" aria-selected="false" tabIndex={-1}>
                      <i className="bar"></i>
                      <b>{t("landing.how.step4Title")}</b>
                      <span>{t("landing.how.step4Desc")}</span>
                    </button>
                  </div>
                  <div className="view">
                    <div className="vi" id="vi"></div>
                  </div>
                </div>
              </div>
            </div>

            {/* Flyer Elements */}
            <div className="flyer" id="flyer">
              <figure className="shot s-l" role="tabpanel" id="tp0" aria-labelledby="tb0">
                <img
                  src="/images/landing/hero-assistants-grid.png"
                  alt="SourceMind assistants dashboard"
                  width={1100}
                  height={640}
                />
              </figure>

              <figure className="shot s-r" role="tabpanel" id="tp2" aria-labelledby="tb2">
                <img
                  src="/images/landing/hero-chat-preview.png"
                  alt="SourceMind chat with an answer and source citations"
                  width={1100}
                  height={640}
                />
              </figure>

              <figure className="shot s-c" role="tabpanel" id="tp1" aria-labelledby="tb1">
                <img
                  src="/images/landing/hero-knowledge-base.png"
                  alt="SourceMind knowledge base with indexed documents"
                  width={1100}
                  height={640}
                />
              </figure>

              <figure className="shot s-m" role="tabpanel" id="tp3" aria-labelledby="tb3">
                <img
                  src="/images/landing/hero-citations-detail.png"
                  alt="SourceMind answer with expanded source citations"
                  width={1100}
                  height={640}
                />
              </figure>
              <div className="fl f1 hx">
                <i></i>{t("landing.mock.badgeVerified")}
              </div>
              <div className="fl f2 hx">
                <i className="am"></i>{t("landing.mock.badgeNoEvidence")}
              </div>
              <div className="fl f3 hx">
                <i className="c3"></i>{t("landing.mock.badgeIndexed")}
              </div>
              <div className="ans hx" aria-label="Exemplo de resposta com fonte">
                <p className="q seq" style={{ "--d": "0.8s" } as React.CSSProperties}>
                  {t("landing.mock.question")}
                </p>
                <div className="trl seq" style={{ "--d": "1.5s" } as React.CSSProperties}>
                  <span>
                    <i></i>{t("landing.mock.hybridSearch")}
                  </span>
                  <span>
                    <i></i>{t("landing.mock.sufficientEvidence")}
                  </span>
                </div>
                <p className="a seq" style={{ "--d": "2.2s" } as React.CSSProperties}>
                  <span className="mk">{t("landing.mock.answer")}</span>
                  <sup>1</sup>
                </p>
                <div className="src seq" style={{ "--d": "3s" } as React.CSSProperties}>
                  <span className="fi">PDF</span>
                  <span>
                    {t("landing.mock.docPdf")}<small>{t("landing.mock.page14")}</small>
                  </span>
                </div>
              </div>
              <p className="cap hx">{t("landing.mock.disclaimer")}</p>
            </div>
          </div>
        </div>

        {/* Problem vs Solution Comparison Section */}
        <section id="problema" className="sec ps">
          <div className="pin2">
            <div className="w pb2">
              <div>
                <h2>{t("landing.problem.title")}</h2>
                <p className="t l pp" style={{ marginTop: "20px" }}>
                  {t("landing.problem.lead1")}
                </p>
                <p className="t l sp" style={{ marginTop: "16px", color: "var(--text)" }}>
                  {t("landing.problem.lead2")}
                </p>
              </div>
              <div className="cv2">
                <div className="qbar">
                  <span className="qt" data-q={t("landing.problem.query")}>
                    {t("landing.problem.query")}
                  </span>
                  <i></i>
                </div>
                <div className="g4">
                  <div className="fcd">
                    <span className="bdg">1</span>
                    <div className="fh">
                      <span className="fi">PDF</span>{t("landing.problem.doc1Name")}
                    </div>
                    <span className="hl">{t("landing.problem.doc1Text")}</span>
                  </div>
                  <div className="fcd">
                    <span className="bdg">2</span>
                    <div className="fh">
                      <span className="fi d">DOCX</span>{t("landing.problem.doc2Name")}
                    </div>
                    <span className="hl">{t("landing.problem.doc2Text")}</span>
                  </div>
                  <div className="fcd x">
                    <div className="fh">
                      <span className="fi d">DOCX</span>{t("landing.problem.doc3Name")}
                    </div>
                    {t("landing.problem.doc3Text")}
                  </div>
                  <div className="fcd x">
                    <div className="fh">
                      <span className="fi">PDF</span>{t("landing.problem.doc4Name")}
                    </div>
                    {t("landing.problem.doc4Text")}
                    <p className="nt">{t("landing.problem.doc4Note")}</p>
                  </div>
                </div>
                <div className="gl ac2">
                  <p style={{ margin: 0 }}>
                    <span className="s1">
                      {t("landing.problem.ans1")}<sup className="cs">1</sup>
                    </span>{" "}
                    <span className="s2">
                      {t("landing.problem.ans2")}<sup className="cs">2</sup>
                    </span>
                  </p>
                  <div className="sr">
                    <span>{t("landing.problem.src1")}</span>
                    <span>{t("landing.problem.src2")}</span>
                  </div>
                </div>
                <div className="cap3">
                  <span className="c1">{t("landing.problem.step1")}</span>
                  <span className="c2">{t("landing.problem.step2")}</span>
                  <span className="c3">{t("landing.problem.step3")}</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Confidence & Abstention Section */}
        <section id="confianca" className="sec">
          <div className="w">
            <h2 style={{ maxWidth: "16ch" }}>{t("landing.confidence.title")}</h2>
            <p className="t l" style={{ marginTop: "16px" }}>
              {t("landing.confidence.subtitle")}
            </p>
            <div className="bento">
              <div className="gl cell big rvv" id="abst">
                <div className="bub">{t("landing.confidence.abstQuery")}</div>
                <div className="rep">
                  <span className="fg">{t("landing.confidence.abstBadge")}</span>
                  <br />
                  {t("landing.confidence.abstText")}
                </div>
                <div style={{ marginTop: "16px", padding: "10px 14px", background: "var(--tint)", borderRadius: "8px", fontSize: "12px", color: "var(--muted)" }}>
                  <b>{t("landing.confidence.abstNote")}</b>
                </div>
              </div>
              <div className="gl cell">
                <h3>{t("landing.confidence.card1Title")}</h3>
                <p className="t">{t("landing.confidence.card1Text")}</p>
                <div className="src" style={{ marginTop: "18px" }}>
                  <span className="fi">PDF</span>
                  <span>
                    {t("landing.mock.docPdf")}<small>{t("landing.mock.page14")}</small>
                  </span>
                </div>
                <p className="ex2">
                  <span className="mk2">{t("landing.confidence.card1Snippet")}</span>
                </p>
              </div>
              <div className="gl cell">
                <h3>{t("landing.confidence.card2Title")}</h3>
                <p className="t">{t("landing.confidence.card2Text")}</p>
                <div className="ln">
                  <span>{t("landing.confidence.hybridVector")}</span>
                  <i className="tr">
                    <u style={{ width: "76%" }}></u>
                  </i>
                </div>
                <div className="ln">
                  <span>{t("landing.confidence.hybridText")}</span>
                  <i className="tr">
                    <u style={{ width: "48%" }}></u>
                  </i>
                </div>
                <div className="ln rk">
                  <span>{t("landing.confidence.hybridRerank")}</span>
                  <b>1</b>
                  <b>2</b>
                  <b>3</b>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Feature Grid Section */}
        <section className="sec">
          <div className="w">
            <h2 style={{ maxWidth: "18ch" }}>{t("landing.control.title")}</h2>
            <div className="g12">
              <div className="gl tl s7">
                <div>
                  <h3>{t("landing.control.card1Title")}</h3>
                  <p className="t">{t("landing.control.card1Text")}</p>
                </div>
                <div className="mini">
                  <div className="mrow">
                    <span className="av">R</span>{t("landing.control.deptHR")}<em className="ok">{t("landing.control.statusReady")}</em>
                  </div>
                  <div className="mrow">
                    <span className="av">J</span>{t("landing.control.deptLegal")}<em className="ok">{t("landing.control.statusReady")}</em>
                  </div>
                </div>
              </div>
              <div className="gl tl s5">
                <div>
                  <h3>{t("landing.control.card2Title")}</h3>
                  <p className="t">{t("landing.control.card2Text")}</p>
                </div>
                <div className="mini">
                  <div className="mrow">
                    <span className="fi">PDF</span>politica_rh.pdf<em className="ok">{t("landing.control.statusIndexed")}</em>
                  </div>
                  <div className="mrow">
                    <span className="fi d">DOCX</span>manual_onboarding.docx<em className="ok">{t("landing.control.statusIndexed")}</em>
                  </div>
                </div>
              </div>
              <div className="gl tl s5">
                <div>
                  <h3>{t("landing.control.card3Title")}</h3>
                  <p className="t">{t("landing.control.card3Text")}</p>
                </div>
                <div className="mini">
                  <div className="key">
                    sk-or-••••••••••••••<em className="ok">{t("landing.control.statusEncrypted")}</em>
                  </div>
                </div>
              </div>
              <div className="gl tl s7">
                <div>
                  <h3>{t("landing.control.card4Title")}</h3>
                  <p className="t">{t("landing.control.card4Text")}</p>
                </div>
                <div className="mini lg">
                  <span className={language === "en" ? "on" : ""}>English</span>
                  <span className={language === "pt" ? "on" : ""}>Português</span>
                  <span className={language === "es" ? "on" : ""}>Español</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* CTA Section */}
        <section id="comecar" className="sec" style={{ paddingTop: "40px" }}>
          <div className="w">
            <div className="cta2 gl">
              <h2>{t("landing.finalCta.title")}</h2>
              <Link className="pb" href={ctaHref}>
                {t("landing.finalCta.button")}
                <i>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                    <path d="M3 8h10M9 4l4 4-4 4" />
                  </svg>
                </i>
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer>{t("landing.footer")}</footer>
    </>
  );
}
