import { FileText, PlayCircle, Question } from "@phosphor-icons/react";
import { PRODUCT_NAVIGATION } from "./navigation";

const NAV_ICONS = { prep: PlayCircle, reports: FileText, help: Question };

export function MobileNav({ pathname = window.location.pathname, onNavigate }) {
  return (
    <nav className="mobile-nav" aria-label="移动端主导航">
      <div className="mobile-nav-inner">
        {PRODUCT_NAVIGATION.map((item) => {
          const current = item.match.includes(pathname);
          const Icon = NAV_ICONS[item.icon] || PlayCircle;
          return (
            <a
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              onClick={(event) => {
                if (onNavigate?.(item.href, item) === false) event.preventDefault();
              }}
            >
              <Icon size={20} weight={current ? "fill" : "bold"} aria-hidden="true" focusable="false" />
              <span>{item.mobileLabel}</span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}
