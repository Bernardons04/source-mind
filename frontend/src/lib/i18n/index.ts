import en from "./en.json";
import pt from "./pt.json";
import es from "./es.json";

export type Language = "en" | "pt" | "es";

export const translations = { en, pt, es };

export function getNestedTranslation(obj: any, path: string): string {
  const parts = path.split(".");
  let current = obj;
  for (const part of parts) {
    if (current && typeof current === "object" && part in current) {
      current = current[part];
    } else {
      return path;
    }
  }
  return typeof current === "string" ? current : path;
}

export function translate(
  lang: Language,
  key: string,
  params?: Record<string, string | number>
): string {
  const dict = translations[lang] || translations.en;
  let text = getNestedTranslation(dict, key);
  if (!text || text === key) {
    text = getNestedTranslation(translations.en, key);
  }

  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      text = text.replace(new RegExp(`{{${k}}}`, "g"), String(v));
    });
  }
  return text;
}
