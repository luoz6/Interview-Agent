import { PRODUCT_NAVIGATION } from "./navigation";

function navClickHandler(item, onNavigate) {
  return (event) => {
    if (!onNavigate) return;
    if (onNavigate(item.href, item) === false) event.preventDefault();
  };
}

export function PrimaryNav({ pathname = window.location.pathname, onNavigate }) {
  return (
    <nav className="app-nav start-nav" aria-label="主导航">
      {PRODUCT_NAVIGATION.map((item) => (
        <a
          key={item.href}
          href={item.href}
          aria-current={item.match.includes(pathname) ? "page" : undefined}
          onClick={navClickHandler(item, onNavigate)}
        >
          {item.label}
        </a>
      ))}
    </nav>
  );
}
