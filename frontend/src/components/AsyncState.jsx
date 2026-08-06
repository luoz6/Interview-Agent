export function AsyncState({
  className = "empty-state",
  tone,
  role,
  live,
  label,
  icon,
  eyebrow,
  title,
  description,
  action,
  children,
  ...props
}) {
  return (
    <div
      className={className}
      data-tone={tone || undefined}
      role={role}
      aria-live={live}
      aria-label={label}
      aria-atomic={role === "alert" ? "true" : undefined}
      {...props}
    >
      {icon}
      {eyebrow && <span className="mono-label">{eyebrow}</span>}
      {title && <h3>{title}</h3>}
      {description && <p>{description}</p>}
      {children}
      {action}
    </div>
  );
}
