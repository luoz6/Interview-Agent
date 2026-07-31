import { useEffect } from "react";
import { pageThemes } from "../designTokens";

export function usePageMeta({ title, description, theme = "research", bodyClass }) {
  const pageTheme = pageThemes[theme] || pageThemes.research;
  const resolvedBodyClass = bodyClass || pageTheme.bodyClass;

  useEffect(() => {
    document.title = `${title} - 面试智能体`;
    document.body.className = resolvedBodyClass;

    const descriptionMeta = document.querySelector('meta[name="description"]');
    const themeMeta = document.querySelector('meta[name="theme-color"]');
    descriptionMeta?.setAttribute("content", description);
    themeMeta?.setAttribute("content", pageTheme.color);

    return () => {
      document.body.className = "";
    };
  }, [title, description, pageTheme.color, resolvedBodyClass]);
}
