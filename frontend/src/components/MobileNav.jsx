import { PRODUCT_NAVIGATION } from "./navigation";

function NavIcon({ name, active }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: active ? "currentColor" : "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };
  if (name === "reports") {
    return <svg {...common}><path d="M6 3.75h9l3 3v13.5H6z" /><path d="M15 3.75v3h3M9 11h6M9 15h6" /></svg>;
  }
  if (name === "help") {
    return <svg {...common}><circle cx="12" cy="12" r="8.25" /><path d="M9.75 9.5a2.4 2.4 0 0 1 4.65.8c0 1.7-2.4 2-2.4 3.7M12 17.25h.01" /></svg>;
  }
  return <svg {...common}><circle cx="12" cy="12" r="8.25" /><path d="m10.25 8.75 5 3.25-5 3.25z" /></svg>;
}

export function MobileNav({ pathname = window.location.pathname, onNavigate }) {
  return (
    <nav className="mobile-nav" aria-label="移动端主导航">
      <div className="mobile-nav-inner">
        {PRODUCT_NAVIGATION.map((item) => {
          const current = item.match.includes(pathname);
          return (
            <a
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              onClick={(event) => {
                if (onNavigate?.(item.href, item) === false) event.preventDefault();
              }}
            >
              <NavIcon name={item.icon} active={current} />
              <span>{item.mobileLabel}</span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}
