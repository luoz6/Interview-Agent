import { navigationClickHandler, PRODUCT_NAVIGATION } from "./navigation";

export function PrimaryNav({ pathname = window.location.pathname, onNavigate }) {
  return (
    <nav className="start-nav" aria-label="主导航">
      {PRODUCT_NAVIGATION.map((item) => (
        <a
          key={item.href}
          href={item.href}
          aria-current={item.match.includes(pathname) ? "page" : undefined}
          onClick={navigationClickHandler(item, onNavigate)}
        >
          {item.label}
        </a>
      ))}
    </nav>
  );
}
