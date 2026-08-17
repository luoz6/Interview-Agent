export const PRODUCT_NAVIGATION = [
  {
    href: "/prep",
    label: "准备",
    match: ["/", "/prep", "/interview"],
  },
  {
    href: "/reports",
    label: "报告",
    match: ["/reports", "/report-processing", "/report-detail"],
  },
  {
    href: "/materials",
    label: "我的资料",
    match: ["/materials"],
  },
  {
    href: "/memory-center",
    label: "我的记忆",
    match: ["/memory-center", "/memory-center.html"],
  },
  {
    href: "/help",
    label: "帮助",
    match: ["/help"],
  },
];

export function navigationClickHandler(item, onNavigate) {
  return (event) => {
    if (onNavigate?.(item.href, item) === false) event.preventDefault();
  };
}
