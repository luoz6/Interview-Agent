import { FileText, PlayCircle, Question } from "@phosphor-icons/react";
import { navigationClickHandler, PRODUCT_NAVIGATION } from "./navigation";

export function MobileNav({ pathname = window.location.pathname, onNavigate }) {
  return (
    <nav className="mobile-nav" aria-label="移动端主导航">
      <div className="mobile-nav-inner">
        {PRODUCT_NAVIGATION.map((item) => {
          const current = item.match.includes(pathname);
          const Icon = item.href === "/prep"
            ? PlayCircle
            : item.href === "/help" ? Question : FileText;
          return (
            <a
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              onClick={navigationClickHandler(item, onNavigate)}
            >
              <Icon size={20} weight={current ? "fill" : "bold"} aria-hidden="true" focusable="false" />
              <span>{item.label}</span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}
